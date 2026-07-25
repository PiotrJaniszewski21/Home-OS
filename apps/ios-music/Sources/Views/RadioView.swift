import SwiftUI

struct RadioView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var radio: RadioStore
    @State private var query = ""

    var body: some View {
        NavigationStack {
            List {
                if query.isEmpty, !radio.favourites.isEmpty {
                    Section("Favourites") {
                        ForEach(radio.favourites) { station in
                            RadioStationRow(station: station)
                        }
                    }
                }

                Section(query.isEmpty ? "Popular in the UK" : "Stations") {
                    let stations = query.isEmpty ? radio.featured : radio.results
                    if stations.isEmpty, !radio.isLoading {
                        ContentUnavailableView(
                            query.isEmpty ? "No Stations Available" : "No Stations Found",
                            systemImage: "radio",
                            description: Text(query.isEmpty ? "Pull to refresh live radio." : "Try another station name or genre.")
                        )
                    } else {
                        ForEach(stations) { station in
                            RadioStationRow(station: station)
                        }
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle("Radio")
            .searchable(text: $query, prompt: "Stations and genres")
            .onSubmit(of: .search) {
                Task { await radio.search(query, using: session.client) }
            }
            .onChange(of: query) { _, value in
                if value.isEmpty { radio.results = [] }
            }
            .overlay { if radio.isLoading { ProgressView() } }
            .refreshable { await radio.load(using: session.client) }
            .task { if radio.featured.isEmpty { await radio.load(using: session.client) } }
            .alert("Radio Unavailable", isPresented: Binding(
                get: { radio.error != nil },
                set: { if !$0 { radio.error = nil } }
            )) {
                Button("OK") { radio.error = nil }
            } message: {
                Text(radio.error ?? "")
            }
        }
    }
}

private struct RadioStationRow: View {
    @EnvironmentObject private var radio: RadioStore
    @EnvironmentObject private var player: PlayerManager
    let station: RadioStation

    private var isPlaying: Bool {
        player.currentRadioStation?.id == station.id && player.isPlaying
    }

    var body: some View {
        HStack(spacing: 14) {
            Button {
                Task { await player.play(station) }
            } label: {
                HStack(spacing: 14) {
                    RadioArtwork(station: station)
                        .frame(width: 62, height: 62)
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 7) {
                            if player.currentRadioStation?.id == station.id {
                                Image(systemName: isPlaying ? "waveform" : "pause.fill")
                                    .foregroundStyle(Color.homeMusicRed)
                                    .symbolEffect(.variableColor.iterative, isActive: isPlaying)
                            }
                            Text(station.name).font(.headline).lineLimit(1)
                        }
                        Text(station.subtitle)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        HStack(spacing: 6) {
                            Text("LIVE").font(.caption2.bold()).foregroundStyle(.red)
                            if station.bitrate > 0 {
                                Text("\(station.bitrate) kbps").font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                    }
                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Button {
                radio.toggleFavourite(station)
            } label: {
                Image(systemName: radio.favouriteIDs.contains(station.id) ? "star.fill" : "star")
                    .foregroundStyle(radio.favouriteIDs.contains(station.id) ? .yellow : .secondary)
                    .frame(width: 40, height: 44)
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, 4)
    }
}

private struct RadioArtwork: View {
    let station: RadioStation

    var body: some View {
        RemoteArtworkView(
            url: station.artwork.hasPrefix("https://") ? station.artwork : "",
            placeholderSymbol: "radio.fill",
            placeholderColors: [.red, .purple]
        )
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}
