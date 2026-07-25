import AVKit
import SwiftUI

struct MusicMiniPlayerView: View {
    @EnvironmentObject private var player: MusicPlayerManager
    let open: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { proxy in
                Rectangle()
                    .fill(Color.homeOSMusicAccent)
                    .frame(
                        width: proxy.size.width
                            * min(player.elapsed / max(player.duration, 1), 1)
                    )
            }
            .frame(height: 2)

            HStack(spacing: 12) {
                if let track = player.currentTrack {
                    Button(action: open) {
                        HStack(spacing: 11) {
                            MusicArtworkView(track: track)
                                .frame(width: 42, height: 42)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(track.title)
                                    .font(.headline)
                                    .lineLimit(1)
                                Text(track.artist)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    Button(action: player.playPrevious) {
                        Image(systemName: "backward.fill")
                    }
                    .buttonStyle(.borderless)
                    .disabled(player.currentRadioStation != nil)

                    Button(action: player.togglePlayback) {
                        if player.isBuffering {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                                .font(.title3)
                        }
                    }
                    .buttonStyle(.borderless)
                    .frame(width: 30)

                    Button(action: player.playNext) {
                        Image(systemName: "forward.fill")
                    }
                    .buttonStyle(.borderless)
                    .disabled(player.currentRadioStation != nil)

                    Button(action: open) {
                        Image(systemName: "rectangle.expand.vertical")
                    }
                    .buttonStyle(.borderless)
                    .help("Open Now Playing")
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial)
        }
    }
}

struct MusicNowPlayingView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var player: MusicPlayerManager
    @EnvironmentObject private var radio: MusicRadioStore
    @State private var showingQueue = false
    @State private var selectedArtistID: String?
    @State private var isResolvingArtist = false
    @State private var artistError: String?

    var body: some View {
        NavigationStack {
            GeometryReader { geometry in
                ZStack {
                    MusicPlayerBackground(url: player.currentTrack?.thumbnail)
                    HStack(spacing: 42) {
                        MusicPlayerArtworkView(image: player.artworkImage)
                            .frame(
                                width: min(geometry.size.height - 120, geometry.size.width * 0.42),
                                height: min(geometry.size.height - 120, geometry.size.width * 0.42)
                            )
                            .shadow(color: .black.opacity(0.3), radius: 28, y: 14)

                        VStack(alignment: .leading, spacing: 18) {
                            Spacer()
                            if let track = player.currentTrack {
                                trackInformation(track)
                                if player.currentRadioStation == nil {
                                    progress
                                } else {
                                    Label("LIVE RADIO", systemImage: "dot.radiowaves.left.and.right")
                                        .font(.caption.bold())
                                        .foregroundStyle(.red)
                                }
                                transportControls
                                accessoryControls(track)
                                playbackStatus
                            } else {
                                ContentUnavailableView(
                                    "Nothing Playing",
                                    systemImage: "music.note"
                                )
                            }
                            Spacer()
                        }
                        .frame(maxWidth: 480)
                    }
                    .padding(42)
                }
            }
            .frame(minWidth: 820, minHeight: 560)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close", action: dismiss.callAsFunction)
                }
                ToolbarItem(placement: .primaryAction) {
                    nowPlayingMenu
                }
            }
            .navigationDestination(item: $selectedArtistID) { artistID in
                MusicArtistView(artistID: artistID)
            }
        }
        .sheet(isPresented: $showingQueue) {
            MusicQueueView()
                .environmentObject(connection)
                .environmentObject(library)
                .environmentObject(player)
        }
        .alert(
            "Could Not Open Artist",
            isPresented: Binding(
                get: { artistError != nil },
                set: { if !$0 { artistError = nil } }
            )
        ) {
            Button("OK") {
                artistError = nil
            }
        } message: {
            Text(artistError ?? "")
        }
    }

    private func trackInformation(_ track: MusicTrack) -> some View {
        HStack(alignment: .bottom, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text(track.title)
                    .font(.system(size: 32, weight: .bold))
                    .lineLimit(2)
                Button {
                    openArtist(track)
                } label: {
                    HStack(spacing: 7) {
                        Text(track.artist)
                            .font(.title2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        if isResolvingArtist {
                            ProgressView().controlSize(.small)
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(player.currentRadioStation != nil || isResolvingArtist)
            }
            Spacer()
            if let station = player.currentRadioStation {
                Button {
                    radio.toggleFavourite(station)
                } label: {
                    Image(
                        systemName: radio.favouriteIDs.contains(station.id)
                            ? "star.fill"
                            : "star"
                    )
                    .font(.title2)
                    .foregroundStyle(
                        radio.favouriteIDs.contains(station.id) ? .yellow : .primary
                    )
                }
                .buttonStyle(.borderless)
            } else {
                Button {
                    toggleLike(track)
                } label: {
                    Image(
                        systemName: library.isLiked(track)
                            ? "heart.fill"
                            : "heart"
                    )
                    .font(.title2)
                    .foregroundStyle(
                        library.isLiked(track) ? Color.homeOSMusicAccent : .primary
                    )
                }
                .buttonStyle(.borderless)
            }
        }
    }

    private var progress: some View {
        VStack(spacing: 6) {
            MusicScrubber(
                value: Binding(get: { player.elapsed }, set: player.seek),
                range: 0...max(player.duration, 1)
            )
            HStack {
                Text(musicFormatTime(player.elapsed))
                Spacer()
                Text("−\(musicFormatTime(max(player.duration - player.elapsed, 0)))")
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
    }

    private var transportControls: some View {
        HStack {
            if player.currentRadioStation == nil {
                Button(action: player.playPrevious) {
                    Image(systemName: "backward.fill")
                        .font(.system(size: 26))
                        .frame(width: 52, height: 52)
                }
            }
            Spacer()
            Button(action: player.togglePlayback) {
                ZStack {
                    if player.isBuffering {
                        ProgressView().controlSize(.large)
                    } else {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 40, weight: .semibold))
                            .offset(x: player.isPlaying ? 0 : 2)
                    }
                }
                .frame(width: 72, height: 72)
            }
            Spacer()
            if player.currentRadioStation == nil {
                Button(action: player.playNext) {
                    Image(systemName: "forward.fill")
                        .font(.system(size: 26))
                        .frame(width: 52, height: 52)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func accessoryControls(_ track: MusicTrack) -> some View {
        HStack {
            MusicRoutePicker()
                .frame(width: 36, height: 30)
                .help("Choose Audio Output")
            Spacer()
            if player.currentRadioStation == nil {
                Button(action: player.toggleShuffle) {
                    Image(systemName: "shuffle")
                        .foregroundStyle(
                            player.shuffleEnabled ? Color.homeOSMusicAccent : .secondary
                        )
                }
                .buttonStyle(.borderless)
                .help(player.shuffleEnabled ? "Turn Shuffle Off" : "Turn Shuffle On")

                Menu {
                    if library.playlists.isEmpty {
                        Text("Create a playlist in the sidebar first")
                    } else {
                        ForEach(library.playlists) { playlist in
                            Button(playlist.name) {
                                Task {
                                    await library.add(
                                        track,
                                        to: playlist,
                                        using: connection.client
                                    )
                                }
                            }
                        }
                    }
                } label: {
                    Image(systemName: "text.badge.plus")
                }
                .menuStyle(.borderlessButton)
                .frame(width: 30)
                .help("Add to Playlist")

                Button(action: player.cycleRepeatMode) {
                    Image(systemName: player.repeatMode.systemImage)
                        .foregroundStyle(
                            player.repeatMode == .off
                                ? .secondary
                                : Color.homeOSMusicAccent
                        )
                }
                .buttonStyle(.borderless)
                .help(player.repeatMode.accessibilityLabel)

                Button {
                    showingQueue = true
                } label: {
                    Image(systemName: "list.bullet")
                }
                .buttonStyle(.borderless)
                .help("Playing Next")
            }
        }
        .font(.title3)
    }

    @ViewBuilder
    private var playbackStatus: some View {
        switch player.playbackState {
        case .loading:
            Text("Loading audio…")
                .foregroundStyle(.secondary)
        case .failed(let message):
            HStack {
                Text(message)
                    .foregroundStyle(.red)
                Button("Try Again") {
                    if let track = player.currentTrack {
                        Task { await player.play(track) }
                    }
                }
            }
        default:
            EmptyView()
        }
    }

    private var nowPlayingMenu: some View {
        Menu {
            if let track = player.currentTrack {
                if let station = player.currentRadioStation {
                    Button {
                        radio.toggleFavourite(station)
                    } label: {
                        Label(
                            radio.favouriteIDs.contains(station.id)
                                ? "Remove Favourite"
                                : "Favourite Station",
                            systemImage: "star"
                        )
                    }
                } else {
                    Button {
                        toggleLike(track)
                    } label: {
                        Label(
                            library.isLiked(track) ? "Remove Love" : "Love",
                            systemImage: "heart"
                        )
                    }
                    Menu("Add to Playlist") {
                        ForEach(library.playlists) { playlist in
                            Button(playlist.name) {
                                Task {
                                    await library.add(
                                        track,
                                        to: playlist,
                                        using: connection.client
                                    )
                                }
                            }
                        }
                    }
                    Button {
                        player.playLater(track)
                    } label: {
                        Label("Play Last", systemImage: "text.append")
                    }
                }
            }
        } label: {
            Label("More", systemImage: "ellipsis.circle")
        }
    }

    private func toggleLike(_ track: MusicTrack) {
        Task {
            if let updated = await library.toggleLike(track, using: connection.client) {
                player.updateCurrentTrack(updated)
            }
        }
    }

    private func openArtist(_ track: MusicTrack) {
        guard player.currentRadioStation == nil else { return }
        if let artistID = track.artistID, !artistID.isEmpty {
            selectedArtistID = artistID
            return
        }
        guard let client = connection.client else {
            artistError = "Home OS is not connected."
            return
        }
        let artistName = track.artist
            .split(separator: ",", maxSplits: 1)
            .first
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            ?? track.artist
        isResolvingArtist = true
        Task {
            do {
                let artists = try await client.musicSearchArtists(artistName)
                let match = artists.first {
                    $0.name.compare(
                        artistName,
                        options: [.caseInsensitive, .diacriticInsensitive]
                    ) == .orderedSame
                } ?? artists.first
                isResolvingArtist = false
                if let match {
                    selectedArtistID = match.id
                } else {
                    artistError = "No artist page was found for \(artistName)."
                }
            } catch {
                isResolvingArtist = false
                artistError = error.localizedDescription
            }
        }
    }
}

private struct MusicScrubber: View {
    @Binding var value: Double
    let range: ClosedRange<Double>
    @State private var isDragging = false

    private var fraction: Double {
        guard range.upperBound > range.lowerBound else { return 0 }
        return min(
            max(
                (value - range.lowerBound)
                    / (range.upperBound - range.lowerBound),
                0
            ),
            1
        )
    }

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(.primary.opacity(0.18))
                Capsule()
                    .fill(.primary.opacity(isDragging ? 0.95 : 0.72))
                    .frame(width: proxy.size.width * fraction)
            }
            .frame(height: isDragging ? 9 : 5)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { gesture in
                        isDragging = true
                        let position = min(
                            max(gesture.location.x / max(proxy.size.width, 1), 0),
                            1
                        )
                        value = range.lowerBound
                            + position * (range.upperBound - range.lowerBound)
                    }
                    .onEnded { _ in
                        withAnimation(.easeOut(duration: 0.16)) {
                            isDragging = false
                        }
                    }
            )
            .animation(.easeOut(duration: 0.16), value: isDragging)
        }
        .frame(height: 18)
        .accessibilityElement()
        .accessibilityLabel("Playback position")
        .accessibilityValue("\(Int(fraction * 100)) percent")
        .accessibilityAdjustableAction { direction in
            let step = max((range.upperBound - range.lowerBound) * 0.05, 5)
            switch direction {
            case .increment:
                value = min(value + step, range.upperBound)
            case .decrement:
                value = max(value - step, range.lowerBound)
            @unknown default:
                break
            }
        }
    }
}

private struct MusicPlayerBackground: View {
    let url: String?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color(nsColor: .windowBackgroundColor)
                if let url, let artworkURL = url.highResolutionMusicArtworkURL {
                    AsyncImage(url: artworkURL) { image in
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                            .blur(radius: 60)
                            .opacity(0.34)
                    } placeholder: {
                        Color.clear
                    }
                }
                Rectangle().fill(.ultraThinMaterial)
                LinearGradient(
                    colors: [.clear, Color(nsColor: .windowBackgroundColor).opacity(0.35)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
        }
        .ignoresSafeArea()
    }
}

private struct MusicRoutePicker: NSViewRepresentable {
    func makeNSView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.isRoutePickerButtonBordered = false
        return view
    }

    func updateNSView(_ nsView: AVRoutePickerView, context: Context) {}
}

private struct MusicQueueView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var player: MusicPlayerManager

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Playing Next")
                    .font(.title2.bold())
                Spacer()
                Toggle(isOn: $player.autoplayEnabled) {
                    Label("Autoplay", systemImage: "infinity")
                }
                .toggleStyle(.button)
                Button("Done") {
                    dismiss()
                }
            }
            .padding()

            List {
                if let current = player.currentTrack {
                    Section("Now Playing") {
                        HStack(spacing: 12) {
                            MusicArtworkView(track: current)
                                .frame(width: 48, height: 48)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(current.title)
                                    .font(.headline)
                                Text(current.artist)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section("Up Next") {
                    if player.queue.isEmpty {
                        HStack {
                            if player.isExtendingQueue {
                                ProgressView()
                                Text("Finding similar songs…")
                            } else {
                                Text("The queue is empty.")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    } else {
                        ForEach(player.queue) { track in
                            MusicTrackRow(track: track, context: player.queue)
                        }
                        .onMove(perform: player.moveQueue)
                        .onDelete(perform: player.removeFromQueue)
                    }
                }
            }
        }
        .frame(minWidth: 560, minHeight: 520)
    }
}
