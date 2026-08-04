import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var session: AppSession
    @State private var history: [Track] = []

    var body: some View {
        NavigationStack {
            List {
                Section("Recently Played") {
                    ForEach(history) { TrackRow(track: $0, context: history) }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Library")
            .overlay {
                if history.isEmpty {
                    ContentUnavailableView("No Music Yet", systemImage: "music.note.list", description: Text("Songs you play appear here."))
                }
            }
            .task { history = (try? await session.client?.history()) ?? [] }
            .refreshable { history = (try? await session.client?.history()) ?? [] }
        }
    }
}
