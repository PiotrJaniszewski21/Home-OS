import AVKit
import MediaPlayer
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
                let isCompactHeight = geometry.size.height < 680
                let artworkSize = min(
                    geometry.size.width - 64,
                    geometry.size.height * (isCompactHeight ? 0.35 : 0.42),
                    360
                )

                VStack(spacing: 0) {
                    playerHeader
                        .padding(.horizontal, 24)
                        .padding(.top, 16)

                    Spacer(minLength: 8)

                    if let track = player.currentTrack {
                        PlayerArtworkView(image: player.artworkImage, isPlaying: player.isPlaying)
                            .frame(width: artworkSize, height: artworkSize)
                            .shadow(color: .black.opacity(0.45), radius: 28, x: 0, y: 16)
                            .scaleEffect(player.isPlaying ? 1.0 : 0.86)
                            .animation(
                                .spring(response: 0.45, dampingFraction: 0.75, blendDuration: 0),
                                value: player.isPlaying
                            )

                        Spacer(minLength: 16)

                        trackInformation(track)
                            .padding(.horizontal, 30)

                        if player.currentRadioStation == nil {
                            progress
                                .padding(.horizontal, 30)
                                .padding(.top, isCompactHeight ? 12 : 20)
                        } else {
                            HStack(spacing: 8) {
                                Circle().fill(.red).frame(width: 8, height: 8)
                                Text("LIVE BROADCAST").font(.caption.bold()).tracking(1)
                            }
                            .padding(.top, isCompactHeight ? 12 : 20)
                        }

                        transportControls
                            .padding(.top, isCompactHeight ? 10 : 16)

                        if player.currentRadioStation == nil {
                            volumeSlider
                                .padding(.horizontal, 34)
                                .padding(.top, isCompactHeight ? 12 : 22)
                        }

                        accessoryControls
                            .padding(.horizontal, 40)
                            .padding(.top, isCompactHeight ? 12 : 20)

                        playbackStatus
                            .padding(.horizontal, 30)
                            .padding(.top, 6)
                    } else {
                        ContentUnavailableView("Nothing Playing", systemImage: "music.note")
                    }

                    Spacer(minLength: isCompactHeight ? 12 : 24)
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
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
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 36, height: 36)
                    .background(.white.opacity(0.18), in: Circle())
            }
            Spacer()
            VStack(spacing: 2) {
                Text(headerCategoryTitle.uppercased())
                    .font(.caption2.bold())
                    .tracking(0.8)
                    .foregroundStyle(.white.opacity(0.75))
                Text(player.currentTrack?.artist ?? "HomeMusic")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white)
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
                            Label(
                                track.liked == true ? "Unlike" : "Love",
                                systemImage: track.liked == true ? "heart.slash" : "heart"
                            )
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
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 36, height: 36)
                    .background(.white.opacity(0.18), in: Circle())
            }
        }
        .buttonStyle(.plain)
    }

    private var headerCategoryTitle: String {
        if player.currentRadioStation != nil {
            return "Live Radio"
        }
        return "Playing Now"
    }

    private func trackInformation(_ track: Track) -> some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text(track.title)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Button {
                    openArtist(track)
                } label: {
                    HStack(spacing: 6) {
                        Text(track.artist)
                            .lineLimit(1)
                        if isResolvingArtist {
                            ProgressView()
                                .controlSize(.mini)
                        }
                    }
                    .font(.title3.weight(.medium))
                    .foregroundStyle(.white)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(player.currentRadioStation != nil || isResolvingArtist)
            }
            Spacer()
            if let station = player.currentRadioStation {
                Button { radio.toggleFavourite(station) } label: {
                    Image(systemName: radio.favouriteIDs.contains(station.id) ? "star.fill" : "star")
                        .font(.title2)
                        .foregroundStyle(radio.favouriteIDs.contains(station.id) ? .yellow : .white.opacity(0.85))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
            } else {
                Button { Task { await player.toggleLike() } } label: {
                    Image(systemName: track.liked == true ? "heart.fill" : "heart")
                        .font(.title2)
                        .foregroundStyle(track.liked == true ? Color.homeMusicRed : .white.opacity(0.85))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var progress: some View {
        VStack(spacing: 8) {
            AppleMusicScrubber(
                value: player.elapsed,
                range: 0...max(player.duration, 1),
                onSeek: player.seek
            )
            HStack {
                Text(formatTime(player.elapsed))
                Spacer()
                Text("−\(formatTime(max(player.duration - player.elapsed, 0)))")
            }
            .font(.caption.weight(.medium).monospacedDigit())
            .foregroundStyle(.white.opacity(0.8))
        }
    }

    private var transportControls: some View {
        HStack(spacing: 48) {
            if player.currentRadioStation == nil {
                Button(action: player.playPrevious) {
                    Image(systemName: "backward.fill")
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 52, height: 52)
                }
            } else {
                Color.clear.frame(width: 52, height: 52)
            }

            Button(action: player.togglePlayback) {
                ZStack {
                    if player.isBuffering {
                        ProgressView().controlSize(.large).tint(.white)
                    } else {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 44, weight: .bold))
                            .foregroundStyle(.white)
                            .offset(x: player.isPlaying ? 0 : 3)
                    }
                }
                .frame(width: 72, height: 72)
            }

            if player.currentRadioStation == nil {
                Button(action: player.playNext) {
                    Image(systemName: "forward.fill")
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 52, height: 52)
                }
            } else {
                Color.clear.frame(width: 52, height: 52)
            }
        }
        .buttonStyle(.plain)
    }

    private var volumeSlider: some View {
        HStack(spacing: 14) {
            Image(systemName: "speaker.fill")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.8))
            SystemVolumeSlider()
                .frame(height: 22)
            Image(systemName: "speaker.wave.3.fill")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.8))
        }
    }

       private var accessoryControls: some View {
        HStack {
            AirPlayButton().frame(width: 44, height: 44)
            Spacer()
            if player.currentRadioStation == nil {
                Button { showingQueue = true } label: {
                    Image(systemName: "list.bullet")
                        .font(.title3.weight(.medium))
                        .foregroundStyle(.white)
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
            Text("Loading audio…").font(.caption).foregroundStyle(.white.opacity(0.8))
        case .failed(let message):
            VStack(spacing: 8) {
                Text(message).font(.caption).foregroundStyle(.red).multilineTextAlignment(.center)
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
        let mins = Int(seconds) / 60
        let secs = Int(seconds) % 60
        return String(format: "%d:%02d", mins, secs)
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
                await MainActor.run {
                    isResolvingArtist = false
                    if let match {
                        selectedArtistID = match.id
                    } else {
                        showArtistError("No artist page was found for \(artistName).")
                    }
                }
            } catch {
                await MainActor.run {
                    isResolvingArtist = false
                    showArtistError(error.localizedDescription)
                }
            }
        }
    }

    private func showArtistError(_ message: String) {
        artistErrorMessage = message
        showingArtistError = true
    }
}

private struct AppleMusicScrubber: View {
    let value: Double
    let range: ClosedRange<Double>
    let onSeek: (Double) -> Void
    @State private var isDragging = false
    @State private var dragStartValue: Double = 0
    @State private var scrubValue: Double = 0

    private var displayValue: Double {
        isDragging ? scrubValue : value
    }

    private var fraction: Double {
        guard range.upperBound > range.lowerBound else { return 0 }
        return min(max((displayValue - range.lowerBound) / (range.upperBound - range.lowerBound), 0), 1)
    }

    var body: some View {
        GeometryReader { proxy in
            let totalSeconds = max(range.upperBound - range.lowerBound, 1)

            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.25))
                Capsule()
                    .fill(Color.white.opacity(isDragging ? 1.0 : 0.88))
                    .frame(width: proxy.size.width * fraction)
            }
            .frame(height: isDragging ? 10 : 4)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 4)
                    .onChanged { gesture in
                        if !isDragging {
                            isDragging = true
                            dragStartValue = value
                        }
                        let deltaFraction = gesture.translation.width / max(proxy.size.width, 1)
                        let deltaSeconds = deltaFraction * totalSeconds
                        let newTime = min(max(dragStartValue + deltaSeconds, range.lowerBound), range.upperBound)
                        scrubValue = newTime
                    }
                    .onEnded { _ in
                        if isDragging {
                            let finalSeek = scrubValue
                            onSeek(finalSeek)
                            Task {
                                try? await Task.sleep(for: .milliseconds(400))
                                withAnimation(.easeOut(duration: 0.18)) {
                                    isDragging = false
                                }
                            }
                        }
                    }
            )
            .animation(.easeOut(duration: 0.16), value: isDragging)
        }
        .frame(height: 18)
    }
}

private struct SystemVolumeSlider: UIViewRepresentable {
    func makeUIView(context: Context) -> MPVolumeView {
        let volumeView = MPVolumeView(frame: .zero)
        volumeView.showsRouteButton = false
        for view in volumeView.subviews {
            if let slider = view as? UISlider {
                slider.tintColor = UIColor.white
                slider.thumbTintColor = UIColor.white
                slider.maximumTrackTintColor = UIColor.white.withAlphaComponent(0.25)
            }
        }
        return volumeView
    }

    func updateUIView(_ uiView: MPVolumeView, context: Context) {}
}

extension UIImage {
    fileprivate func extractPaletteColors() -> [Color] {
        guard let cgImage = self.cgImage else {
            return [.pink, .purple, .cyan, Color(red: 1.0, green: 0.1, blue: 0.8), .orange]
        }
        let width = 6
        let height = 6
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        var rawData = [UInt8](repeating: 0, count: width * height * 4)

        guard let context = CGContext(
            data: &rawData,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return [.pink, .purple, .cyan, Color(red: 1.0, green: 0.1, blue: 0.8), .orange]
        }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        var colors: [Color] = []
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * 4
                let r = Double(rawData[offset]) / 255.0
                let g = Double(rawData[offset + 1]) / 255.0
                let b = Double(rawData[offset + 2]) / 255.0
                let brightness = (r + g + b) / 3.0
                if brightness > 0.12 && brightness < 0.92 {
                    colors.append(Color(red: r, green: g, blue: b))
                }
            }
        }

        return colors.isEmpty ? [.pink, .purple, .cyan, Color(red: 1.0, green: 0.1, blue: 0.8), .orange] : colors
    }
}

struct PlayerBackground: View {
    let url: String?
    @EnvironmentObject private var player: PlayerManager
    @State private var blurredThumbnail: UIImage?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                // Base Dark Atmosphere
                Color.black

                // Downscaled Blurred Album Cover Layer
                if let blurredThumbnail {
                    Image(uiImage: blurredThumbnail)
                        .resizable()
                        .scaledToFill()
                        .frame(width: proxy.size.width, height: proxy.size.height)
                        .clipped()
                        .saturation(1.4)
                        .blur(radius: 16)
                        .opacity(0.45)
                }

                // Subtle Edge Vignette
                RadialGradient(
                    colors: [.clear, Color.black.opacity(0.60)],
                    center: .center,
                    startRadius: proxy.size.width * 0.30,
                    endRadius: proxy.size.width * 0.85
                )
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
            .task(id: player.artworkImage) {
                guard let artwork = player.artworkImage else {
                    blurredThumbnail = nil
                    return
                }
                let processed = await Task.detached(priority: .utility) {
                    let size = CGSize(width: 96, height: 96)
                    UIGraphicsBeginImageContextWithOptions(size, false, 1.0)
                    artwork.draw(in: CGRect(origin: .zero, size: size))
                    let downscaled = UIGraphicsGetImageFromCurrentImageContext()
                    UIGraphicsEndImageContext()
                    return downscaled
                }.value
                await MainActor.run {
                    blurredThumbnail = processed
                }
            }
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
