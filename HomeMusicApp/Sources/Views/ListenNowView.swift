import SwiftUI

@MainActor
final class ListenNowModel: ObservableObject {
    @Published var recommendations: [Track] = []
    @Published var recent: [Track] = []
    @Published var isLoading = false

    func load(using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        async let recommendationsRequest = client.recommendations()
        async let historyRequest = client.history()
        recommendations = (try? await recommendationsRequest) ?? []
        recent = (try? await historyRequest) ?? []
        isLoading = false
    }
}

struct ListenNowView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @StateObject private var model = ListenNowModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 28) {
                    if model.recommendations.isEmpty && !model.isLoading {
                        ContentUnavailableView(
                            "Your station is warming up",
                            systemImage: "waveform.circle",
                            description: Text("Play a few songs and HomeMusic will build suggestions around your taste.")
                        )
                        .frame(minHeight: 250)
                    } else {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Made for You").font(.title2.bold())
                            ScrollView(.horizontal, showsIndicators: false) {
                                LazyHStack(spacing: 16) {
                                    ForEach(model.recommendations) { track in
                                        Button { Task { await player.play(track, from: model.recommendations) } } label: {
                                            VStack(alignment: .leading, spacing: 8) {
                                                ArtworkView(track: track).frame(width: 180, height: 180)
                                                Text(track.title).font(.headline).lineLimit(1)
                                                Text(track.artist).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                                            }.frame(width: 180, alignment: .leading)
                                        }.buttonStyle(.plain)
                                    }
                                }
                            }
                        }
                    }
                    if !model.recent.isEmpty {
                        VStack(alignment: .leading, spacing: 14) {
                            Text("Recently Played").font(.title2.bold())
                            ForEach(model.recent.prefix(10)) { TrackRow(track: $0, context: model.recent) }
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Listen Now")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Sign Out", role: .destructive, action: session.signOut)
                    } label: { Image(systemName: "person.crop.circle") }
                }
            }
            .refreshable { await model.load(using: session.client) }
            .task { await model.load(using: session.client) }
        }
    }
}
