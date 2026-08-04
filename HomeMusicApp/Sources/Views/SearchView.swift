import SwiftUI

@MainActor
final class SearchModel: ObservableObject {
    @Published var query = ""
    @Published var results: [Track] = []
    @Published var isSearching = false
    @Published var error: String?

    func search(using client: APIClient?) async {
        guard let client, !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        isSearching = true
        defer { isSearching = false }
        do {
            results = try await client.search(query)
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

struct SearchView: View {
    @EnvironmentObject private var session: AppSession
    @StateObject private var model = SearchModel()

    var body: some View {
        NavigationStack {
            Group {
                if model.results.isEmpty && !model.isSearching {
                    ContentUnavailableView.search(text: model.query)
                } else {
                    List(model.results) { TrackRow(track: $0, context: model.results) }
                        .listStyle(.plain)
                }
            }
            .navigationTitle("Search")
            .searchable(text: $model.query, prompt: "Artists, songs and more")
            .onSubmit(of: .search) { Task { await model.search(using: session.client) } }
            .overlay { if model.isSearching { ProgressView() } }
        }
    }
}
