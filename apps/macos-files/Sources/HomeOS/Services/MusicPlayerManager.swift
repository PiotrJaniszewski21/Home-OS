import AppKit
import AVFoundation
import MediaPlayer
import SwiftUI

enum MusicPlaybackState: Equatable {
    case idle
    case loading
    case playing
    case paused
    case failed(String)
}

enum MusicRepeatMode: String, CaseIterable {
    case off
    case all
    case one

    var systemImage: String {
        self == .one ? "repeat.1" : "repeat"
    }

    var accessibilityLabel: String {
        switch self {
        case .off: "Repeat Off"
        case .all: "Repeat All"
        case .one: "Repeat One"
        }
    }

    var next: MusicRepeatMode {
        switch self {
        case .off: .all
        case .all: .one
        case .one: .off
        }
    }
}

@MainActor
final class MusicPlayerManager: ObservableObject {
    @Published private(set) var currentTrack: MusicTrack?
    @Published private(set) var currentRadioStation: MusicRadioStation?
    @Published private(set) var isPlaying = false
    @Published private(set) var elapsed: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var playbackError: String?
    @Published private(set) var playbackState: MusicPlaybackState = .idle
    @Published private(set) var isBuffering = false
    @Published private(set) var artworkImage: NSImage?
    @Published private(set) var isExtendingQueue = false
    @Published private(set) var previewTrackID: String?
    @Published private(set) var previewProgress: Double = 0
    @Published var autoplayEnabled = true
    @Published var repeatMode: MusicRepeatMode = .off
    @Published var shuffleEnabled = false
    @Published var queue: [MusicTrack] = []

    private let player = AVPlayer()
    private let previewPlayer = AVPlayer()
    private weak var client: APIClient?
    private var previousTracks: [MusicTrack] = []
    private var sourceTracks: [MusicTrack] = []
    private var playedTrackIDs = Set<String>()
    private var preparedSources: [String: (source: MusicPlaybackSource, preparedAt: Date)] = [:]
    private var timeObserver: Any?
    private var previewTimeObserver: Any?
    private var completionObserver: NSObjectProtocol?
    private var failureObserver: NSObjectProtocol?
    private var stalledObserver: NSObjectProtocol?
    private var completedItem: AVPlayerItem?
    private var isAdvancingQueue = false
    private var historyRecordedForTrack: String?
    private var previewStopTask: Task<Void, Never>?
    private var resumeAfterPreview = false
    private var playbackRequestID = UUID()
    private var previewRequestID = UUID()
    private var playbackFallbackURL: URL?
    private var isUsingPlaybackFallback = false
    private var authoritativeDuration: Double?
    private let previewStartSeconds: Double = 60

    private static let artworkCache = NSCache<NSString, NSImage>()

    init() {
        player.automaticallyWaitsToMinimizeStalling = true
        configureRemoteCommands()
        observeTime()
    }

    deinit {
        if let timeObserver {
            player.removeTimeObserver(timeObserver)
        }
        if let previewTimeObserver {
            previewPlayer.removeTimeObserver(previewTimeObserver)
        }
        previewStopTask?.cancel()
        if let completionObserver {
            NotificationCenter.default.removeObserver(completionObserver)
        }
        if let failureObserver {
            NotificationCenter.default.removeObserver(failureObserver)
        }
        if let stalledObserver {
            NotificationCenter.default.removeObserver(stalledObserver)
        }
    }

    func connect(client: APIClient?) {
        if self.client !== client {
            preparedSources.removeAll()
        }
        self.client = client
    }

    func play(_ track: MusicTrack, from tracks: [MusicTrack] = []) async {
        currentRadioStation = nil
        sourceTracks = tracks.isEmpty ? [track] : tracks
        playedTrackIDs = []
        let remaining = remainingTracks(after: track, in: sourceTracks)
        await start(track, remaining: shuffleEnabled ? remaining.shuffled() : remaining)
    }

    func playShuffled(_ tracks: [MusicTrack]) async {
        let shuffled = tracks.shuffled()
        guard let first = shuffled.first else { return }
        shuffleEnabled = true
        await play(first, from: shuffled)
    }

    func play(_ station: MusicRadioStation) async {
        guard let url = URL(string: station.streamURL) else {
            setPlaybackFailure("This station has an invalid stream address.")
            return
        }
        stopPreview(resumeMainPlayer: false)
        playbackRequestID = UUID()
        playbackFallbackURL = nil
        isUsingPlaybackFallback = false
        playbackError = nil
        playbackState = .loading
        isBuffering = true
        currentRadioStation = station
        currentTrack = station.playerTrack
        queue = []
        previousTracks = []
        sourceTracks = []
        playedTrackIDs = []
        elapsed = 0
        duration = 0
        authoritativeDuration = nil
        historyRecordedForTrack = nil
        prepareArtwork(for: station.playerTrack)

        let asset = AVURLAsset(
            url: url,
            options: [
                "AVURLAssetHTTPHeaderFieldsKey": [
                    "User-Agent": "HomeOSMac/1.0",
                    "Icy-MetaData": "1",
                    "Accept": "audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.5",
                ]
            ]
        )
        let item = AVPlayerItem(asset: asset)
        item.preferredForwardBufferDuration = 2
        completedItem = nil
        player.replaceCurrentItem(with: item)
        observeFailure(of: item)
        observeStatus(of: item)
        player.playImmediately(atRate: 1)
        updateNowPlaying()
    }

    func togglePlayback() {
        guard currentTrack != nil else { return }
        if player.timeControlStatus == .playing {
            player.pause()
            isPlaying = false
            playbackState = .paused
        } else {
            player.playImmediately(atRate: 1)
            isPlaying = true
            playbackState = .playing
        }
        updateNowPlaying()
    }

    func seek(to value: Double) {
        let target = max(0, duration > 0 ? min(value, duration) : value)
        player.seek(to: CMTime(seconds: target, preferredTimescale: 600))
        elapsed = target
        updateNowPlaying()
    }

    func playNext() {
        guard currentRadioStation == nil else { return }
        Task { await advanceQueue() }
    }

    func playPrevious() {
        guard currentRadioStation == nil else { return }
        guard elapsed < 3, let previous = previousTracks.popLast() else {
            seek(to: 0)
            return
        }
        let remaining = currentTrack.map { [$0] + queue } ?? queue
        Task { await start(previous, remaining: remaining) }
    }

    func playNext(_ track: MusicTrack) {
        queue.removeAll { $0.id == track.id }
        queue.insert(track, at: 0)
    }

    func playLater(_ track: MusicTrack) {
        queue.removeAll { $0.id == track.id }
        queue.append(track)
    }

    func removeFromQueue(at offsets: IndexSet) {
        queue.remove(atOffsets: offsets)
    }

    func moveQueue(from offsets: IndexSet, to destination: Int) {
        queue.move(fromOffsets: offsets, toOffset: destination)
    }

    func toggleShuffle() {
        shuffleEnabled.toggle()
        if shuffleEnabled {
            queue.shuffle()
        }
    }

    func cycleRepeatMode() {
        repeatMode = repeatMode.next
    }

    func updateCurrentTrack(_ track: MusicTrack) {
        guard currentTrack?.id == track.id else { return }
        currentTrack = track
        updateNowPlaying()
    }

    func togglePreview(_ track: MusicTrack) async {
        if previewTrackID == track.id {
            stopPreview(resumeMainPlayer: true)
            return
        }
        stopPreview(resumeMainPlayer: false)
        guard let client else { return }
        let requestID = UUID()
        previewRequestID = requestID
        do {
            let source = try await client.musicPlayback(for: track)
            guard previewRequestID == requestID else { return }
            resumeAfterPreview = player.timeControlStatus == .playing
            if resumeAfterPreview {
                player.pause()
            }
            previewTrackID = track.id
            previewProgress = 0
            let item = AVPlayerItem(url: source.url)
            item.preferredForwardBufferDuration = 4
            previewPlayer.replaceCurrentItem(with: item)
            observePreviewTime()
            await previewPlayer.seek(
                to: CMTime(seconds: previewStartSeconds, preferredTimescale: 600),
                toleranceBefore: .zero,
                toleranceAfter: .zero
            )
            previewPlayer.playImmediately(atRate: 1)
            previewStopTask = Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled else { return }
                self?.stopPreview(resumeMainPlayer: true)
            }
        } catch {
            guard previewRequestID == requestID else { return }
            playbackError = "Preview is temporarily unavailable."
        }
    }

    func stopPreview(resumeMainPlayer: Bool = true) {
        previewRequestID = UUID()
        previewStopTask?.cancel()
        previewStopTask = nil
        previewPlayer.pause()
        previewPlayer.replaceCurrentItem(with: nil)
        if let previewTimeObserver {
            previewPlayer.removeTimeObserver(previewTimeObserver)
            self.previewTimeObserver = nil
        }
        previewTrackID = nil
        previewProgress = 0
        if resumeMainPlayer, resumeAfterPreview {
            player.playImmediately(atRate: 1)
        }
        resumeAfterPreview = false
    }

    func stop() {
        playbackRequestID = UUID()
        stopPreview(resumeMainPlayer: false)
        player.pause()
        player.replaceCurrentItem(with: nil)
        completedItem = nil
        isAdvancingQueue = false
        currentTrack = nil
        currentRadioStation = nil
        queue = []
        previousTracks = []
        sourceTracks = []
        playedTrackIDs = []
        artworkImage = nil
        elapsed = 0
        duration = 0
        authoritativeDuration = nil
        isPlaying = false
        isBuffering = false
        playbackError = nil
        playbackState = .idle
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    private func start(_ track: MusicTrack, remaining: [MusicTrack]) async {
        let requestID = UUID()
        playbackRequestID = requestID
        do {
            playbackError = nil
            playbackState = .loading
            isBuffering = true
            let source = try await playbackSource(for: track)
            guard playbackRequestID == requestID else { return }
            if let currentTrack, currentTrack.id != track.id {
                previousTracks.append(currentTrack)
            }
            currentTrack = track
            currentRadioStation = nil
            playedTrackIDs.insert(track.id)
            prepareArtwork(for: track)
            queue = remaining
            historyRecordedForTrack = nil
            elapsed = 0
            let reportedDuration = source.durationSeconds ?? Double(track.durationSeconds ?? 0)
            duration = reportedDuration
            authoritativeDuration = reportedDuration > 0 ? reportedDuration : nil
            playbackFallbackURL = source.fallbackURL
            isUsingPlaybackFallback = false

            let item = AVPlayerItem(url: source.url)
            item.preferredForwardBufferDuration = 8
            completedItem = nil
            player.replaceCurrentItem(with: item)
            observeCompletion(of: item)
            observeFailure(of: item)
            observeStatus(of: item)
            player.playImmediately(atRate: 1)
            updateNowPlaying()
            prefetchSources(for: Array(remaining.prefix(2)))
        } catch {
            guard playbackRequestID == requestID else { return }
            setPlaybackFailure(error.localizedDescription)
        }
    }

    private func playbackSource(for track: MusicTrack) async throws -> MusicPlaybackSource {
        if let prepared = preparedSources.removeValue(forKey: track.id),
           Date().timeIntervalSince(prepared.preparedAt) < 180 {
            return prepared.source
        }
        guard let client else {
            throw APIError.requestFailed("Home OS is not connected.")
        }
        return try await client.musicPlayback(for: track)
    }

    private func prefetchSources(for tracks: [MusicTrack]) {
        guard let client else { return }
        for track in tracks where preparedSources[track.id] == nil {
            Task { @MainActor [weak self] in
                guard let self, let source = try? await client.musicPlayback(for: track) else { return }
                self.preparedSources[track.id] = (source, Date())
            }
        }
    }

    private func configureRemoteCommands() {
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.addTarget { [weak self] _ in
            Task { @MainActor in
                guard let self, !self.isPlaying else { return }
                self.togglePlayback()
            }
            return .success
        }
        commands.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor in
                guard let self, self.isPlaying else { return }
                self.togglePlayback()
            }
            return .success
        }
        commands.nextTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.playNext() }
            return .success
        }
        commands.previousTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor in self?.playPrevious() }
            return .success
        }
        commands.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let position = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            Task { @MainActor in self?.seek(to: position.positionTime) }
            return .success
        }
    }

    private func observeTime() {
        let interval = CMTime(seconds: 1, preferredTimescale: 1)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            Task { @MainActor in
                guard let self else { return }
                let rawElapsed = max(0, time.seconds.isFinite ? time.seconds : 0)
                let item = self.player.currentItem
                let itemDuration = item?.duration.seconds ?? 0
                if self.authoritativeDuration == nil, itemDuration.isFinite, itemDuration > 0 {
                    self.duration = itemDuration
                }
                self.elapsed = self.duration > 0 ? min(rawElapsed, self.duration) : rawElapsed
                self.synchronizePlaybackState()
                self.updateNowPlaying()
                self.recordHistoryIfNeeded()
                if let item, self.shouldAdvancePastEnd(item, rawElapsed: rawElapsed) {
                    await self.handlePlaybackEnded(item)
                }
            }
        }
    }

    private func observePreviewTime() {
        if let previewTimeObserver {
            previewPlayer.removeTimeObserver(previewTimeObserver)
        }
        let interval = CMTime(seconds: 0.25, preferredTimescale: 4)
        previewTimeObserver = previewPlayer.addPeriodicTimeObserver(
            forInterval: interval,
            queue: .main
        ) { [weak self] time in
            Task { @MainActor in
                guard let self, self.previewTrackID != nil else { return }
                self.previewProgress = min(
                    max((time.seconds - self.previewStartSeconds) / 30, 0),
                    1
                )
            }
        }
    }

    private func observeCompletion(of item: AVPlayerItem) {
        if let completionObserver {
            NotificationCenter.default.removeObserver(completionObserver)
        }
        completionObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                await self?.handlePlaybackEnded(item)
            }
        }
    }

    private func observeFailure(of item: AVPlayerItem) {
        if let failureObserver {
            NotificationCenter.default.removeObserver(failureObserver)
        }
        if let stalledObserver {
            NotificationCenter.default.removeObserver(stalledObserver)
        }
        failureObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor in
                guard let self else { return }
                let error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
                if self.itemHasReachedEnd(item) {
                    await self.handlePlaybackEnded(item)
                } else if !self.playFromFallbackIfAvailable() {
                    self.setPlaybackFailure(error?.localizedDescription ?? "This song could not be played.")
                }
            }
        }
        stalledObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemPlaybackStalled,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.isBuffering = true
                self?.playbackState = .loading
            }
        }
    }

    private func observeStatus(of item: AVPlayerItem) {
        Task { @MainActor [weak self, weak item] in
            guard let self, let item else { return }
            do {
                _ = try await item.asset.load(.isPlayable)
                guard self.player.currentItem === item else { return }
                self.synchronizePlaybackState()
                self.playbackError = nil
                self.updateNowPlaying()
            } catch {
                guard self.player.currentItem === item else { return }
                if !self.playFromFallbackIfAvailable() {
                    self.setPlaybackFailure(error.localizedDescription)
                }
            }
        }
    }

    private func playFromFallbackIfAvailable() -> Bool {
        guard currentRadioStation == nil,
              !isUsingPlaybackFallback,
              let fallbackURL = playbackFallbackURL else {
            return false
        }
        isUsingPlaybackFallback = true
        let item = AVPlayerItem(url: fallbackURL)
        item.preferredForwardBufferDuration = 4
        completedItem = nil
        player.replaceCurrentItem(with: item)
        observeCompletion(of: item)
        observeFailure(of: item)
        observeStatus(of: item)
        player.playImmediately(atRate: 1)
        return true
    }

    private func synchronizePlaybackState() {
        switch player.timeControlStatus {
        case .playing:
            isPlaying = true
            isBuffering = false
            playbackState = .playing
        case .waitingToPlayAtSpecifiedRate:
            isPlaying = false
            isBuffering = true
            playbackState = .loading
        case .paused:
            isPlaying = false
            isBuffering = false
            if currentTrack != nil, playbackError == nil {
                playbackState = .paused
            }
        @unknown default:
            break
        }
    }

    private func setPlaybackFailure(_ message: String) {
        isPlaying = false
        isBuffering = false
        playbackError = message
        playbackState = .failed(message)
        updateNowPlaying()
    }

    private func advanceQueue() async {
        guard !isAdvancingQueue else { return }
        isAdvancingQueue = true
        defer { isAdvancingQueue = false }

        if repeatMode == .one, let currentTrack {
            await start(currentTrack, remaining: queue)
            return
        }
        if queue.isEmpty, repeatMode == .all, !sourceTracks.isEmpty {
            let restarted = shuffleEnabled ? sourceTracks.shuffled() : sourceTracks
            queue = restarted
        }
        if queue.isEmpty {
            await extendQueue()
        }
        guard let next = queue.first else {
            isPlaying = false
            playbackState = .paused
            updateNowPlaying()
            return
        }
        await start(next, remaining: Array(queue.dropFirst()))
    }

    private func handlePlaybackEnded(_ item: AVPlayerItem) async {
        guard player.currentItem === item, completedItem !== item else { return }
        completedItem = item
        player.pause()
        isPlaying = false
        isBuffering = false
        if duration > 0 {
            elapsed = duration
        }
        updateNowPlaying()
        if let currentTrack, let client {
            let playedSeconds = Int(elapsed)
            Task {
                try? await client.musicRecord(
                    currentTrack,
                    playedSeconds: playedSeconds,
                    completed: true
                )
            }
        }
        await advanceQueue()
    }

    private func itemHasReachedEnd(_ item: AVPlayerItem) -> Bool {
        guard player.currentItem === item else { return false }
        let currentTime = item.currentTime().seconds
        let endTime = effectiveEndTime(for: item)
        guard endTime > 0, currentTime.isFinite else { return false }
        return currentTime >= max(endTime - 0.75, 0)
    }

    private func shouldAdvancePastEnd(_ item: AVPlayerItem, rawElapsed: Double) -> Bool {
        let endTime = effectiveEndTime(for: item)
        guard endTime > 0 else { return false }
        if player.timeControlStatus == .paused {
            return rawElapsed >= max(endTime - 0.75, 0)
        }
        return rawElapsed >= endTime + 0.75
    }

    private func effectiveEndTime(for item: AVPlayerItem) -> Double {
        if let authoritativeDuration,
           authoritativeDuration.isFinite,
           authoritativeDuration > 0 {
            return authoritativeDuration
        }
        let itemDuration = item.duration.seconds
        return itemDuration.isFinite && itemDuration > 0 ? itemDuration : 0
    }

    private func extendQueue() async {
        guard autoplayEnabled,
              !isExtendingQueue,
              let client,
              let currentTrack else {
            return
        }
        isExtendingQueue = true
        defer { isExtendingQueue = false }
        let contextSeeds = sourceTracks.isEmpty ? [currentTrack.id] : sourceTracks.map(\.id)
        let seeds = Array((contextSeeds + [currentTrack.id]).uniqued().suffix(3))
        let excluded = Array(Set(contextSeeds).union(playedTrackIDs).union(queue.map(\.id)))
        do {
            let suggestions = try await client.musicRecommendations(
                seedIDs: seeds,
                excluding: excluded,
                limit: 15
            )
            queue.append(contentsOf: suggestions.filter { suggestion in
                !playedTrackIDs.contains(suggestion.id)
                    && !queue.contains { $0.id == suggestion.id }
            })
        } catch {
            playbackError = "Could not load more recommendations."
        }
    }

    private func remainingTracks(after track: MusicTrack, in tracks: [MusicTrack]) -> [MusicTrack] {
        guard let index = tracks.firstIndex(where: { $0.id == track.id }) else {
            return []
        }
        return Array(tracks.dropFirst(index + 1))
    }

    private func recordHistoryIfNeeded() {
        guard elapsed >= 30,
              historyRecordedForTrack != currentTrack?.id,
              let currentTrack,
              let client else {
            return
        }
        historyRecordedForTrack = currentTrack.id
        let playedSeconds = Int(elapsed)
        Task {
            try? await client.musicRecord(
                currentTrack,
                playedSeconds: playedSeconds,
                completed: false
            )
        }
    }

    private func updateNowPlaying(artwork: MPMediaItemArtwork? = nil) {
        guard let track = currentTrack else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        var info = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        info[MPMediaItemPropertyTitle] = track.title
        info[MPMediaItemPropertyArtist] = track.artist
        if currentRadioStation == nil {
            info[MPMediaItemPropertyPlaybackDuration] = duration
            info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = elapsed
            info[MPNowPlayingInfoPropertyIsLiveStream] = false
        } else {
            info.removeValue(forKey: MPMediaItemPropertyPlaybackDuration)
            info.removeValue(forKey: MPNowPlayingInfoPropertyElapsedPlaybackTime)
            info[MPNowPlayingInfoPropertyIsLiveStream] = true
        }
        info[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? 1.0 : 0.0
        info[MPNowPlayingInfoPropertyExternalContentIdentifier] = track.id
        if let artwork {
            info[MPMediaItemPropertyArtwork] = artwork
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func prepareArtwork(for track: MusicTrack) {
        let artworkURL = track.thumbnail.highResolutionMusicArtworkURL
        let cacheKey = (artworkURL?.absoluteString ?? track.thumbnail) as NSString
        if let cached = Self.artworkCache.object(forKey: cacheKey) {
            artworkImage = cached
            updateNowPlaying(artwork: nowPlayingArtwork(from: cached))
            return
        }
        artworkImage = nil
        guard let artworkURL else { return }
        Task {
            guard let (data, _) = try? await URLSession.shared.data(from: artworkURL),
                  let image = NSImage(data: data),
                  currentTrack?.id == track.id else {
                return
            }
            Self.artworkCache.setObject(image, forKey: cacheKey)
            artworkImage = image
            updateNowPlaying(artwork: nowPlayingArtwork(from: image))
        }
    }

    private func nowPlayingArtwork(from image: NSImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: image.size) { _ in image }
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
