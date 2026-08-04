import AVKit
import SwiftUI

struct NowPlayingView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var player: PlayerManager
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    @EnvironmentObject private var radio: RadioStore
    @State private var showingQueue = false
    @State private var selectedArtistID: String?
    @State private var isResolvingArtist = false
    @State private var artistErrorMessage = ""
    @State private var showingArtistError = false

    var body: some View {
        NavigationStack {
            GeometryReader { geometry in
                let artworkSize = min(geometry.size.width - 48, geometry.size.height * 0.42, 380)

                ZStack {
                    PlayerBackground(url: player.currentTrack?.thumbnail)
                        .frame(width: geometry.size.width, height: geometry.size.height)
                        .clipped()

                    VStack(spacing: 0) {
                        playerHeader
                            .padding(.horizontal, 20)
                            .padding(.top, 8)

                        Spacer(minLength: 12)

                        if let track = player.currentTrack {
                            PlayerArtworkView(image: player.artworkImage)
                                .frame(width: artworkSize, height: artworkSize)
                                .shadow(color: .black.opacity(0.32), radius: 28, y: 16)
                                .scaleEffect(player.isPlaying ? 1 : 0.92)
                                .animation(.spring(response: 0.5, dampingFraction: 0.82), value: player.isPlaying)

                            Spacer(minLength: 24)

                            trackInformation(track)
                                .padding(.horizontal, 28)

                            if player.currentRadioStation == nil {
                                progress
                                    .padding(.horizontal, 28)
                                    .padding(.top, 22)
                            } else {
                                HStack(spacing: 8) {
                                    Circle().fill(.red).frame(width: 8, height: 8)
                                    Text("LIVE RADIO").font(.caption.bold())
                                }
                                .padding(.top, 22)
                            }

                            transportControls
                                .padding(.top, 14)

                            accessoryControls
                                .padding(.horizontal, 38)
                                .padding(.top, 14)

                            playbackStatus
                                .padding(.horizontal, 28)
                                .padding(.top, 8)
                        } else {
                            ContentUnavailableView("Nothing Playing", systemImage: "music.note")
                        }

                        Spacer(minLength: 18)
                    }
                    .frame(width: geometry.size.width, height: geometry.size.height)
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
                .clipped()
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(item: $selectedArtistID) { artistID in
                ArtistView(artistID: artistID)
                    .toolbar(.visible, for: .navigationBar)
            }
        }
        .ignoresSafeArea(edges: .bottom)
        .sheet(isPresented: $showingQueue) { QueueView() }
        .alert("Could Not Open Artist", isPresented: $showingArtistError) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(artistErrorMessage)
        }
    }

    private var playerHeader: some View {
        HStack {
            Button { dismiss() } label: {
                Image(systemName: "chevron.down")
                    .font(.headline.bold())
                    .frame(width: 44, height: 44)
            }
            Spacer()
            VStack(spacing: 2) {
                Text("PLAYING NOW")
                    .font(.caption2.bold())
                    .foregroundStyle(.secondary)
                Text(player.currentTrack?.artist ?? "HomeMusic")
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
            }
            Spacer()
            Menu {
                if let track = player.currentTrack {
                    if let station = player.currentRadioStation {
                        Button { radio.toggleFavourite(station) } label: {
                            Label(
                                radio.favouriteIDs.contains(station.id) ? "Remove Favourite" : "Favourite Station",
                                systemImage: radio.favouriteIDs.contains(station.id) ? "star.slash" : "star"
                            )
                        }
                    } else {
                        Button { Task { await player.toggleLike() } } label: {
                            Label("Love", systemImage: "heart")
                        }
                    }
                    if player.currentRadioStation == nil, !library.playlists.isEmpty {
                        Menu("Add to Playlist") {
                            ForEach(library.playlists) { playlist in
                                Button(playlist.name) {
                                    Task {
                                        await library.add(track, to: playlist, using: session.client)
                                    }
                                }
                            }
                        }
                    }
                    if player.currentRadioStation == nil {
                        if offlineMusic.activeTrackIDs.contains(track.id) {
                            Label("Downloading…", systemImage: "arrow.down.circle")
                        } else if offlineMusic.isDownloaded(track) {
                            Button(role: .destructive) {
                                offlineMusic.removeDownload(track)
                            } label: {
                                Label("Remove Download", systemImage: "trash")
                            }
                        } else {
                            Button {
                                Task { await offlineMusic.download(track) }
                            } label: {
                                Label("Download", systemImage: "arrow.down.circle")
                            }
                        }
                        Button { player.playLater(track) } label: {
                            Label("Play Last", systemImage: "text.append")
                        }
                    }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.headline)
                    .frame(width: 44, height: 44)
            }
        }
        .buttonStyle(.plain)
    }

    private func trackInformation(_ track: Track) -> some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text(track.title)
                    .font(.title2.bold())
                    .lineLimit(1)
                Button {
                    openArtist(track)
                } label: {
                    HStack(spacing: 7) {
                        Text(track.artist)
                            .lineLimit(1)
                        if isResolvingArtist {
                            ProgressView()
                                .controlSize(.mini)
                        }
                    }
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(player.currentRadioStation != nil || isResolvingArtist)
                .accessibilityHint("Opens the artist page")
            }
            Spacer()
            if let station = player.currentRadioStation {
                Button { radio.toggleFavourite(station) } label: {
                    Image(systemName: radio.favouriteIDs.contains(station.id) ? "star.fill" : "star")
                        .font(.title2)
                        .foregroundStyle(radio.favouriteIDs.contains(station.id) ? .yellow : .primary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
            } else {
                Button { Task { await player.toggleLike() } } label: {
                    Image(systemName: track.liked == true ? "heart.fill" : "heart")
                        .font(.title2)
                        .foregroundStyle(track.liked == true ? Color.homeMusicRed : .primary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var progress: some View {
        VStack(spacing: 7) {
            AppleMusicScrubber(
                value: Binding(get: { player.elapsed }, set: player.seek),
                range: 0...max(player.duration, 1)
            )
            HStack {
                Text(formatTime(player.elapsed))
                Spacer()
                Text("−\(formatTime(max(player.duration - player.elapsed, 0)))")
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
    }

    private var transportControls: some View {
        HStack(spacing: 52) {
            if player.currentRadioStation == nil {
                Button(action: player.playPrevious) {
                    Image(systemName: "backward.fill")
                        .font(.system(size: 28))
                        .frame(width: 52, height: 52)
                }
            } else {
                Color.clear.frame(width: 52, height: 52)
            }
            Button(action: player.togglePlayback) {
                ZStack {
                    if player.isBuffering {
                        ProgressView().controlSize(.large).tint(.primary)
                    } else {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 44, weight: .semibold))
                            .offset(x: player.isPlaying ? 0 : 2)
                    }
                }
                .frame(width: 76, height: 76)
            }
            if player.currentRadioStation == nil {
                Button(action: player.playNext) {
                    Image(systemName: "forward.fill")
                        .font(.system(size: 28))
                        .frame(width: 52, height: 52)
                }
            } else {
                Color.clear.frame(width: 52, height: 52)
            }
        }
        .buttonStyle(.plain)
    }

    private var accessoryControls: some View {
        HStack {
            AirPlayButton().frame(width: 44, height: 44)
            Spacer()
            if let track = player.currentTrack, player.currentRadioStation == nil {
                Menu {
                    if library.playlists.isEmpty {
                        Text("Create a playlist in Library first")
                    } else {
                        ForEach(library.playlists) { playlist in
                            Button(playlist.name) {
                                Task {
                                    await library.add(track, to: playlist, using: session.client)
                                }
                            }
                        }
                    }
                } label: {
                    Image(systemName: "text.badge.plus")
                        .font(.title3)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Add to Playlist")
            }
            Spacer()
            if player.currentRadioStation == nil {
                Button { showingQueue = true } label: {
                    Image(systemName: "list.bullet")
                        .font(.title3)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
            } else {
                Color.clear.frame(width: 44, height: 44)
            }
        }
    }

    @ViewBuilder
    private var playbackStatus: some View {
        switch player.playbackState {
        case .loading:
            Text("Loading audio…").foregroundStyle(.secondary)
        case .failed(let message):
            VStack(spacing: 8) {
                Text(message).foregroundStyle(.red).multilineTextAlignment(.center)
                Button("Try Again") {
                    if let track = player.currentTrack { Task { await player.play(track) } }
                }
                .buttonStyle(.bordered)
            }
        default:
            EmptyView()
        }
    }

    private func formatTime(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0 else { return "0:00" }
        return "\(Int(seconds) / 60):\(String(format: "%02d", Int(seconds) % 60))"
    }

    private func openArtist(_ track: Track) {
        guard player.currentRadioStation == nil else { return }
        if let artistID = track.artistID, !artistID.isEmpty {
            selectedArtistID = artistID
            return
        }
        guard let client = session.client else {
            showArtistError("Home OS is not connected.")
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
                let artists = try await client.searchArtists(artistName)
                let match = artists.first {
                    $0.name.compare(artistName, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame
                } ?? artists.first
                isResolvingArtist = false
                if let match {
                    selectedArtistID = match.id
                } else {
                    showArtistError("No artist page was found for \(artistName).")
                }
            } catch {
                isResolvingArtist = false
                showArtistError(error.localizedDescription)
            }
        }
    }

    private func showArtistError(_ message: String) {
        artistErrorMessage = message
        showingArtistError = true
    }
}

private struct AppleMusicScrubber: View {
    @Binding var value: Double
    let range: ClosedRange<Double>
    @State private var isDragging = false

    private var fraction: Double {
        guard range.upperBound > range.lowerBound else { return 0 }
        return min(max((value - range.lowerBound) / (range.upperBound - range.lowerBound), 0), 1)
    }

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(.primary.opacity(0.18))
                Capsule()
                    .fill(.primary.opacity(isDragging ? 0.95 : 0.72))
                    .frame(width: proxy.size.width * fraction)
            }
            .frame(height: isDragging ? 10 : 5)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { gesture in
                        isDragging = true
                        let position = min(max(gesture.location.x / max(proxy.size.width, 1), 0), 1)
                        value = range.lowerBound + position * (range.upperBound - range.lowerBound)
                    }
                    .onEnded { _ in
                        withAnimation(.easeOut(duration: 0.18)) { isDragging = false }
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

struct PlayerBackground: View {
    let url: String?
    @State private var artworkImage: UIImage?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color(uiColor: .systemBackground)
                if let artworkImage {
                    Image(uiImage: artworkImage)
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                            .blur(radius: 60)
                            .opacity(0.38)
                }
                Rectangle().fill(.ultraThinMaterial)
                LinearGradient(
                    colors: [.clear, Color(uiColor: .systemBackground).opacity(0.3)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
        .task(id: url) {
            artworkImage = nil
            guard let url,
                  let artworkURL = url.highResolutionMusicArtworkURL else {
                return
            }
            artworkImage = await ArtworkCacheStore.shared.image(for: artworkURL)
        }
    }
}

private struct QueueView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var player: PlayerManager

    var body: some View {
        NavigationStack {
            List {
                if let current = player.currentTrack {
                    Section("Now Playing") {
                        HStack(spacing: 12) {
                            ArtworkView(track: current).frame(width: 52, height: 52)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(current.title).font(.headline).lineLimit(1)
                                Text(current.artist).font(.subheadline).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Section {
                    if player.queue.isEmpty {
                        if player.isExtendingQueue {
                            HStack {
                                ProgressView()
                                Text("Finding similar songs…")
                            }
                        } else {
                            ContentUnavailableView("Queue Is Empty", systemImage: "text.line.last.and.arrowtriangle.forward")
                        }
                    } else {
                        ForEach(player.queue) { track in TrackRow(track: track, context: player.queue) }
                            .onMove { player.queue.move(fromOffsets: $0, toOffset: $1) }
                            .onDelete { player.queue.remove(atOffsets: $0) }
                    }
                } header: {
                    Text("Up Next")
                } footer: {
                    if player.autoplayEnabled {
                        Label("Similar music continues when the queue ends.", systemImage: "infinity")
                    }
                }
            }
            .navigationTitle("Playing Next")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Done") { dismiss() } }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button {
                        player.autoplayEnabled.toggle()
                    } label: {
                        Image(systemName: "infinity")
                            .foregroundStyle(player.autoplayEnabled ? Color.homeMusicRed : .secondary)
                    }
                    EditButton()
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

private struct AirPlayButton: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.prioritizesVideoDevices = false
        view.tintColor = .label
        view.activeTintColor = UIColor(Color.homeMusicRed)
        return view
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
