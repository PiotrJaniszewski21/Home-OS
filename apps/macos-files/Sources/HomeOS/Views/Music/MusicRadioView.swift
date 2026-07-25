import SwiftUI

struct MusicRadioView: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var player: MusicPlayerManager
    @EnvironmentObject private var radio: MusicRadioStore
    @State private var query = ""

    var body: some View {
        NavigationStack {
            List {
                if query.isEmpty, !radio.favourites.isEmpty {
                    Section("Favourites") {
                        ForEach(radio.favourites) { station in
                            stationRow(station)
                        }
                    }
                }

                Section(query.isEmpty ? "Popular in the UK" : "Stations") {
                    let stations = query.isEmpty ? radio.featured : radio.results
                    if stations.isEmpty, !radio.isLoading {
                        ContentUnavailableView(
                            query.isEmpty ? "No Stations Available" : "No Stations Found",
                            systemImage: "radio",
                            description: Text(
                                query.isEmpty
                                    ? "Refresh to load live radio."
                                    : "Try another station name or genre."
                            )
                        )
                    } else {
                        ForEach(stations) { station in
                            stationRow(station)
                        }
                    }
                }
            }
            .navigationTitle("Radio")
            .searchable(text: $query, prompt: "Stations and genres")
            .onSubmit(of: .search) {
                Task { await radio.search(query, using: connection.client) }
            }
            .onChange(of: query) { _, value in
                if value.isEmpty {
                    radio.results = []
                }
            }
            .toolbar {
                Button {
                    Task { await radio.load(using: connection.client) }
                } label: {
                    Label("Refresh Stations", systemImage: "arrow.clockwise")
                }
            }
            .overlay {
                if radio.isLoading {
                    ProgressView()
                }
            }
            .task {
                if radio.featured.isEmpty {
                    await radio.load(using: connection.client)
                }
            }
            .alert(
                "Radio Unavailable",
                isPresented: Binding(
                    get: { radio.error != nil },
                    set: { if !$0 { radio.error = nil } }
                )
            ) {
                Button("OK") {
                    radio.error = nil
                }
            } message: {
                Text(radio.error ?? "")
            }
        }
    }

    private func stationRow(_ station: MusicRadioStation) -> some View {
        HStack(spacing: 14) {
            Button {
                Task { await player.play(station) }
            } label: {
                HStack(spacing: 14) {
                    MusicRemoteArtworkView(
                        url: station.artwork.hasPrefix("https://") ? station.artwork : "",
                        placeholderSymbol: "radio.fill",
                        placeholderColors: [.red, .purple]
                    )
                    .frame(width: 58, height: 58)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 7) {
                            if player.currentRadioStation?.id == station.id {
                                Image(systemName: player.isPlaying ? "waveform" : "pause.fill")
                                    .foregroundStyle(Color.homeOSMusicAccent)
                            }
                            Text(station.name)
                                .font(.headline)
                                .lineLimit(1)
                        }
                        Text(station.subtitle)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        HStack(spacing: 6) {
                            Text("LIVE")
                                .font(.caption2.bold())
                                .foregroundStyle(.red)
                            if station.bitrate > 0 {
                                Text("\(station.bitrate) kbps")
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
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
                Image(
                    systemName: radio.favouriteIDs.contains(station.id)
                        ? "star.fill"
                        : "star"
                )
                .foregroundStyle(
                    radio.favouriteIDs.contains(station.id) ? .yellow : .secondary
                )
                .frame(width: 34, height: 34)
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 3)
    }
}
