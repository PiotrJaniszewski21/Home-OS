import AVFoundation
import MediaPlayer
import OSLog
import UIKit

private let playbackLogger = Logger(
    subsystem: "uk.co.petershomenet.homemusic",
    category: "Playback"
)

enum PlaybackState: Equatable {
    case idle
    case loading
    case playing
    case paused
    case failed(String)
}

private enum PlaybackScenario: String {
    case selection
    case playlistSelection = "playlist_selection"
    case next
    case previous
    case autoplay
}

private enum PlaybackSourceKind: String {
    case downloaded
    case deviceCache = "device_cache"
    case starterCache = "starter_cache"
    case serverCache = "server_cache"
    case providerStream = "provider_stream"
    case fallbackProxy = "fallback_proxy"
}

private struct PlaybackMetricState {
    let eventID = UUID()
    let trackID: String
    let scenario: PlaybackScenario
    let startedAt: ContinuousClock.Instant
    var sourceKind: PlaybackSourceKind = .providerStream
    var sourceReadyMilliseconds: Int?
    var fallbackUsed = false
}

private final class PreparedPlayback {
    let source: PlaybackSource
    let item: AVPlayerItem
    let cachedAsset: CachedAudioAsset?
    let sourceKind: PlaybackSourceKind

    init(
        source: PlaybackSource,
        item: AVPlayerItem,
        cachedAsset: CachedAudioAsset?,
        sourceKind: PlaybackSourceKind
    ) {
        self.source = source
        self.item = item
        self.cachedAsset = cachedAsset
        self.sourceKind = sourceKind
    }
}

@MainActor
final class PlayerManager: ObservableObject {
    @Published private(set) var currentTrack: Track?
    @Published private(set) var currentRadioStation: RadioStation?
    @Published private(set) var isPlaying = false
    @Published private(set) var elapsed: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var playbackError: String?
    @Published private(set) var playbackState: PlaybackState = .idle
    @Published private(set) var isBuffering = false
    @Published private(set) var artworkImage: UIImage?
    @Published private(set) var isExtendingQueue = false
    @Published private(set) var previewTrackID: String?
    @Published private(set) var previewProgress: Double = 0
    @Published var autoplayEnabled = true
    @Published var queue: [Track] = [] {
        didSet {
            prepareForLikelyPlayback(queue)
        }
    }
    private var previousTracks: [Track] = []
    private var sourceTrackIDs: [String] = []
    private var playedTrackIDs = Set<String>()

    private let player = AVPlayer()
    private let previewPlayer = AVPlayer()
    private var timeObserver: Any?
    private var completionObserver: NSObjectProtocol?
    private var failureObserver: NSObjectProtocol?
    private var stalledObserver: NSObjectProtocol?
    private var routeChangeObserver: NSObjectProtocol?
    private var completedItem: AVPlayerItem?
    private var isRecoveringPlaybackFailure = false
    private var consecutivePlaybackFailures = 0
    private var historyRecordedForTrack: String?
    private var previewTimeObserver: Any?
    private var previewStopTask: Task<Void, Never>?
    private var playbackWatchdogTask: Task<Void, Never>?
    private var resumeAfterPreview = false
    private let previewStartSeconds: Double = 60
    private var playbackRequestID = UUID()
    private var previewRequestID = UUID()
    private var preparedPlaybacks: [String: PreparedPlayback] = [:]
    private var preparedSources: [String: PlaybackSource] = [:]
    private var sourcePreparationTask: Task<PlaybackSource?, Never>?
    private var sourcePreparationTrackID: String?
    private var sourcePreparationGeneration = UUID()
    private var automaticCacheTask: Task<Void, Never>?
    private var serverCachePreparationTask: Task<Void, Never>?
    private var recentPlaybackSources: [String: PlaybackSource] = [:]
    private var recentPlaybackTrackIDs: [String] = []
    private var pendingLikelyTracks: [Track] = []
    private var activeCachedAsset: CachedAudioAsset?
    private var playbackFallbackURL: URL?
    private var isUsingPlaybackFallback = false
    private var fallbackDuration: Double?
    private weak var session: AppSession?
    private weak var offlineMusic: OfflineMusicStore?
    private static let artworkCache = NSCache<NSString, UIImage>()
    private var playbackStartedAt: ContinuousClock.Instant?
    private var activePlaybackMetric: PlaybackMetricState?
    private static let maximumConsecutivePlaybackFailures = 3
    private static let localPreparationLimit = 12

    init() {
        player.automaticallyWaitsToMinimizeStalling = false
        configureAudioSession()
        configureRemoteCommands()
        observeAudioRouteChanges()
        observeTime()
    }

    deinit {
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        if let previewTimeObserver { previewPlayer.removeTimeObserver(previewTimeObserver) }
        previewStopTask?.cancel()
        playbackWatchdogTask?.cancel()
        sourcePreparationTask?.cancel()
        serverCachePreparationTask?.cancel()
        if let completionObserver { NotificationCenter.default.removeObserver(completionObserver) }
        if let failureObserver { NotificationCenter.default.removeObserver(failureObserver) }
        if let stalledObserver { NotificationCenter.default.removeObserver(stalledObserver) }
        if let routeChangeObserver { NotificationCenter.default.removeObserver(routeChangeObserver) }
    }

    func connect(session: AppSession, offlineMusic: OfflineMusicStore) {
        self.session = session
        self.offlineMusic = offlineMusic
        StarterAudioCache.shared.connect(client: session.client)
        let pendingTracks = pendingLikelyTracks
        pendingLikelyTracks = []
        prepareForLikelyPlayback(pendingTracks)
    }

    func play(_ track: Track, from tracks: [Track] = []) async {
        currentRadioStation = nil
        sourceTrackIDs = (tracks.isEmpty ? [track] : tracks).map(\.id)
        playedTrackIDs = []
        consecutivePlaybackFailures = 0
        await start(
            track,
            remaining: remainingTracks(after: track, in: tracks),
            scenario: tracks.isEmpty ? .selection : .playlistSelection
        )
    }

    func playFromSearch(_ track: Track) async {
        currentRadioStation = nil
        sourceTrackIDs = [track.id]
        playedTrackIDs = []
        consecutivePlaybackFailures = 0
        await start(
            track,
            remaining: [],
            scenario: .selection
        )
        await extendQueue()
    }

    func play(_ station: RadioStation) async {
        guard let url = URL(string: station.streamURL) else {
            setPlaybackFailure("This station has an invalid stream address.")
            return
        }
        stopPreview(resumeMainPlayer: false)
        playbackRequestID = UUID()
        playbackFallbackURL = nil
        isUsingPlaybackFallback = false
        configureAudioSession()
        playbackError = nil
        playbackState = .loading
        isBuffering = true
        currentRadioStation = station
        currentTrack = station.playerTrack
        queue = []
        previousTracks = []
        sourceTrackIDs = []
        playedTrackIDs = []
        consecutivePlaybackFailures = 0
        elapsed = 0
        duration = 0
        fallbackDuration = nil
        historyRecordedForTrack = nil
        prepareArtwork(for: station.playerTrack)
        let asset = AVURLAsset(
            url: url,
            options: [
                "AVURLAssetHTTPHeaderFieldsKey": [
                    "User-Agent": "HomeMusic/1.0",
                    "Icy-MetaData": "1",
                    "Accept": "audio/aac,audio/mpeg,audio/*;q=0.9,*/*;q=0.5",
                ]
            ]
        )
        let item = AVPlayerItem(asset: asset)
        item.preferredForwardBufferDuration = 2
        completedItem = nil
        replacePlaybackItem(with: item)
        observeFailure(of: item)
        observeStatus(of: item)
        player.playImmediately(atRate: 1)
        updateNowPlaying()
    }

    private func start(
        _ track: Track,
        remaining: [Track],
        scenario: PlaybackScenario
    ) async {
        let requestID = UUID()
        playbackRequestID = requestID
        playbackWatchdogTask?.cancel()
        playbackStartedAt = .now
        activePlaybackMetric = PlaybackMetricState(
            trackID: track.id,
            scenario: scenario,
            startedAt: .now
        )
        do {
            configureAudioSession()
            playbackError = nil
            playbackState = .loading
            isBuffering = true
            let preparation = try await playbackPreparation(for: track)
            let source = preparation.source
            guard playbackRequestID == requestID else { return }
            let sourceReadyMilliseconds = playbackMilliseconds()
            activePlaybackMetric?.sourceKind = preparation.sourceKind
            activePlaybackMetric?.sourceReadyMilliseconds = sourceReadyMilliseconds
            if let playbackStartedAt {
                let elapsed = playbackStartedAt.duration(to: .now)
                playbackLogger.info("Playback source ready in \(elapsed, privacy: .public)")
            }
            if let currentTrack, currentTrack.id != track.id { previousTracks.append(currentTrack) }
            currentTrack = track
            playedTrackIDs.insert(track.id)
            prepareArtwork(for: track)
            queue = remaining
            historyRecordedForTrack = nil
            elapsed = 0
            let canonicalDuration = track.parsedDurationSeconds ?? source.durationSeconds ?? 0
            duration = canonicalDuration
            fallbackDuration = validDuration(canonicalDuration)
            playbackFallbackURL = source.fallbackURL
            isUsingPlaybackFallback = false
            StarterAudioCache.shared.cancelAll()
            player.replaceCurrentItem(with: nil)
            let item = preparation.item
            item.preferredForwardBufferDuration = 0.5
            completedItem = nil
            replacePlaybackItem(with: item)
            activeCachedAsset = preparation.cachedAsset
            observeCompletion(of: item)
            observeFailure(of: item)
            observeStatus(of: item)
            player.playImmediately(atRate: 1)
            schedulePlaybackWatchdog(for: item)
            updateNowPlaying()
            
            let nextRemaining = remaining
            automaticCacheTask?.cancel()
            automaticCacheTask = Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(3))
                guard !Task.isCancelled, let self else { return }
                self.prepareNextPlaybackSource(nextRemaining.first)
                self.prepareForLikelyPlayback(nextRemaining)
                
                try? await Task.sleep(for: .seconds(9))
                guard !Task.isCancelled else { return }
                let automaticTracks = [track] + Array(nextRemaining.prefix(2))
                await self.offlineMusic?.cacheAutomatically(automaticTracks)
            }
        } catch {
            guard playbackRequestID == requestID else { return }
            finishPlaybackMetric(success: false)
            consecutivePlaybackFailures += 1
            if shouldSkipPreparationFailure(error),
               consecutivePlaybackFailures < Self.maximumConsecutivePlaybackFailures,
               let next = remaining.first {
                playbackError = nil
                playbackState = .loading
                isBuffering = true
                await start(
                    next,
                    remaining: Array(remaining.dropFirst()),
                    scenario: scenario
                )
                return
            }
            setPlaybackFailure(playbackFailureMessage(for: error))
        }
    }

    private func shouldSkipPreparationFailure(_ error: Error) -> Bool {
        guard case APIError.http(let status, _) = error else { return false }
        return status == 502
    }

    private func playbackPreparation(for track: Track) async throws -> PreparedPlayback {
        if sourcePreparationTrackID == track.id,
           let sourcePreparationTask {
            let source = await sourcePreparationTask.value
            self.sourcePreparationTask = nil
            sourcePreparationTrackID = nil
            if let source, source.isUsable() {
                preparedSources.removeValue(forKey: track.id)
                return makePreparation(track: track, source: source)
            }
        } else if sourcePreparationTask != nil {
            sourcePreparationTask?.cancel()
            sourcePreparationTask = nil
            sourcePreparationTrackID = nil
        }
        if let prepared = preparedPlaybacks.removeValue(forKey: track.id),
           prepared.source.isUsable() {
            if !prepared.source.url.isFileURL, prepared.cachedAsset == nil {
                return makePreparation(
                    track: track,
                    source: prepared.source
                )
            }
            return prepared
        }
        if let localURL = offlineMusic?.localURL(for: track) {
            return makeLocalPreparation(
                track: track,
                url: localURL,
                durationSeconds: track.durationSeconds.map(Double.init)
            )
        }
        if let source = preparedSources.removeValue(forKey: track.id),
           source.isUsable() {
            return makePreparation(track: track, source: source)
        }
        if let source = recentPlaybackSources[track.id],
           source.isUsable() {
            return makePreparation(track: track, source: source)
        }
        guard let client = session?.client else {
            throw APIError.network(URLError(.notConnectedToInternet))
        }
        let source = try await client.playback(for: track)
        return makePreparation(track: track, source: source)
    }

    private func makeLocalPreparation(
        track: Track,
        url: URL,
        durationSeconds: Double?
    ) -> PreparedPlayback {
        let sourceKind: PlaybackSourceKind = (
            offlineMusic?.isDownloaded(track) == true
                ? .downloaded
                : .deviceCache
        )
        let asset = AVURLAsset(url: url)
        let assetSeconds = CMTimeGetSeconds(asset.duration)
        let effectiveDuration: Double? = (assetSeconds.isFinite && assetSeconds > 0)
            ? assetSeconds
            : (track.parsedDurationSeconds ?? durationSeconds)

        let source = PlaybackSource(
            url: url,
            durationSeconds: effectiveDuration
        )
        return PreparedPlayback(
            source: source,
            item: AVPlayerItem(asset: asset),
            cachedAsset: nil,
            sourceKind: sourceKind
        )
    }

    private func makePreparation(
        track: Track,
        source: PlaybackSource,
        preferredSourceKind: PlaybackSourceKind? = nil
    ) -> PreparedPlayback {
        rememberPlaybackSource(source, for: track.id)
        let authorization = session?.client.flatMap { client in
            source.url.host == client.baseURL.host
                ? "Bearer \(client.token)"
                : nil
        }
        let usesHomeOSProxy = authorization != nil
        if !usesHomeOSProxy {
            StarterAudioCache.shared.warm(
                trackID: track.id,
                sourceURL: source.url,
                authorization: authorization
            )
        }
        let cachedAsset = usesHomeOSProxy
            ? nil
            : CachedAudioAsset(
                trackID: track.id,
                sourceURL: source.url,
                authorization: authorization
            )
        let item = cachedAsset.map { AVPlayerItem(asset: $0.asset) }
            ?? AVPlayerItem(url: source.url)
        let sourceKind = cachedAsset == nil
            ? preferredSourceKind ?? (
                source.cacheHit ? .serverCache : .providerStream
            )
            : .starterCache
        return PreparedPlayback(
            source: source,
            item: item,
            cachedAsset: cachedAsset,
            sourceKind: sourceKind
        )
    }

    private func rememberPlaybackSource(_ source: PlaybackSource, for trackID: String) {
        recentPlaybackSources[trackID] = source
        recentPlaybackTrackIDs.removeAll { $0 == trackID }
        recentPlaybackTrackIDs.append(trackID)
        while recentPlaybackTrackIDs.count > 30 {
            let removedTrackID = recentPlaybackTrackIDs.removeFirst()
            recentPlaybackSources.removeValue(forKey: removedTrackID)
        }
    }

    func prewarmTracks(_ tracks: [Track]) {
        guard let client = session?.client else { return }
        let candidates = Array(tracks.prefix(5))
        Task.detached(priority: .utility) {
            _ = try? await client.prepareServerCache(for: candidates)
        }
    }

    func prepareForLikelyPlayback(_ tracks: [Track]) {
        var seen = Set<String>()
        let candidates = tracks.filter {
            $0.id != currentTrack?.id && seen.insert($0.id).inserted
        }
        guard offlineMusic != nil else {
            pendingLikelyTracks = Array(
                candidates.prefix(Self.localPreparationLimit)
            )
            return
        }
        for track in candidates.prefix(Self.localPreparationLimit) {
            guard preparedPlaybacks[track.id] == nil,
                  let localURL = offlineMusic?.localURL(for: track) else {
                continue
            }
            preparedPlaybacks[track.id] = makeLocalPreparation(
                track: track,
                url: localURL,
                durationSeconds: track.durationSeconds.map(Double.init)
            )
        }
        serverCachePreparationTask?.cancel()
        let serverCandidates = Array(candidates.prefix(Self.localPreparationLimit))
        guard !serverCandidates.isEmpty, let client = session?.client else { return }
        serverCachePreparationTask = Task {
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            try? await client.prepareServerCache(for: serverCandidates)
        }
    }

    private func prepareNextPlaybackSource(_ track: Track?) {
        guard let client = session?.client else { return }
        let upcomingTracks = queue.prefix(3)
        for nextTrack in upcomingTracks {
            guard offlineMusic?.localURL(for: nextTrack) == nil,
                  preparedSources[nextTrack.id]?.isUsable() != true,
                  recentPlaybackSources[nextTrack.id]?.isUsable() != true else {
                continue
            }
            Task { @MainActor [weak self] in
                guard let self,
                      let source = try? await client.playback(for: nextTrack, prefetch: true) else { return }
                self.preparedSources[nextTrack.id] = source
            }
        }
    }

    private func replacePlaybackItem(with item: AVPlayerItem?) {
        player.replaceCurrentItem(with: item)
    }

    func togglePlayback() {
        guard currentTrack != nil else { return }
        if player.timeControlStatus == .playing {
            player.pause()
            isPlaying = false
            playbackState = .paused
        } else {
            configureAudioSession()
            player.playImmediately(atRate: 1)
            isPlaying = true
            playbackState = .playing
        }
        updateNowPlaying()
    }

    func seek(to value: Double) {
        player.seek(to: CMTime(seconds: value, preferredTimescale: 600))
        elapsed = value
        updateNowPlaying()
    }

    func playNext() {
        guard currentRadioStation == nil else { return }
        consecutivePlaybackFailures = 0
        Task { await advanceQueue(scenario: .next) }
    }

    func playPrevious() {
        guard currentRadioStation == nil else { return }
        guard let previous = previousTracks.popLast() else {
            seek(to: 0)
            return
        }
        let remaining = currentTrack.map { [$0] + queue } ?? queue
        consecutivePlaybackFailures = 0
        Task {
            await start(
                previous,
                remaining: remaining,
                scenario: .previous
            )
        }
    }

    func playNext(_ track: Track) {
        var updatedQueue = queue.filter { $0.id != track.id }
        updatedQueue.insert(track, at: 0)
        queue = updatedQueue
    }

    func playLater(_ track: Track) {
        var updatedQueue = queue.filter { $0.id != track.id }
        updatedQueue.append(track)
        queue = updatedQueue
    }

    func toggleLike() async {
        guard currentRadioStation == nil else { return }
        guard let track = currentTrack, let client = session?.client else { return }
        if let updated = try? await client.setLiked(!(track.liked ?? false), track: track) {
            currentTrack = updated
            updateNowPlaying()
        }
    }

    func togglePreview(_ track: Track) async {
        if previewTrackID == track.id {
            stopPreview(resumeMainPlayer: true)
            return
        }
        stopPreview(resumeMainPlayer: false)
        guard let client = session?.client else { return }
        let requestID = UUID()
        previewRequestID = requestID
        do {
            let source = try await client.playback(for: track)
            guard previewRequestID == requestID else { return }
            resumeAfterPreview = player.timeControlStatus == .playing
            if resumeAfterPreview { player.pause() }
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
        playbackWatchdogTask?.cancel()
        stopPreview(resumeMainPlayer: false)
        player.pause()
        replacePlaybackItem(with: nil)
        completedItem = nil
        isRecoveringPlaybackFailure = false
        consecutivePlaybackFailures = 0
        currentTrack = nil
        currentRadioStation = nil
        queue = []
        previousTracks = []
        sourceTrackIDs = []
        playedTrackIDs = []
        sourcePreparationGeneration = UUID()
        sourcePreparationTask?.cancel()
        sourcePreparationTask = nil
        sourcePreparationTrackID = nil
        automaticCacheTask?.cancel()
        automaticCacheTask = nil
        serverCachePreparationTask?.cancel()
        serverCachePreparationTask = nil
        preparedPlaybacks.removeAll()
        preparedSources.removeAll()
        recentPlaybackSources.removeAll()
        recentPlaybackTrackIDs.removeAll()
        pendingLikelyTracks = []
        activeCachedAsset = nil
        StarterAudioCache.shared.connect(client: nil)
        activePlaybackMetric = nil
        artworkImage = nil
        elapsed = 0
        duration = 0
        fallbackDuration = nil
        isPlaying = false
        isBuffering = false
        playbackError = nil
        playbackState = .idle
        playbackStartedAt = nil
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    private func configureAudioSession() {
        do {
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.playback, mode: .default, options: [.allowAirPlay])
            try audio.setActive(true)
        } catch {}
    }

    private func observeAudioRouteChanges() {
        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor in
                guard let self else { return }
                let reasonValue = notification.userInfo?[
                    AVAudioSessionRouteChangeReasonKey
                ] as? UInt
                let reason = reasonValue.flatMap(
                    AVAudioSession.RouteChangeReason.init(rawValue:)
                )
                if reason == .oldDeviceUnavailable {
                    self.player.pause()
                    self.previewPlayer.pause()
                    self.resumeAfterPreview = false
                    self.isPlaying = false
                    self.isBuffering = false
                    if self.currentTrack != nil, self.playbackError == nil {
                        self.playbackState = .paused
                    }
                } else {
                    await Task.yield()
                    self.synchronizePlaybackState()
                }
                self.updateNowPlaying()
            }
        }
    }

    private func observePreviewTime() {
        if let previewTimeObserver { previewPlayer.removeTimeObserver(previewTimeObserver) }
        let interval = CMTime(seconds: 0.25, preferredTimescale: 4)
        previewTimeObserver = previewPlayer.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            Task { @MainActor in
                guard let self, self.previewTrackID != nil else { return }
                self.previewProgress = min(
                    max((time.seconds - self.previewStartSeconds) / 30, 0),
                    1
                )
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
            guard let position = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
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
                if self.duration <= 0, let item {
                    let seconds = CMTimeGetSeconds(item.duration)
                    if seconds.isFinite, seconds > 0 {
                        self.duration = seconds
                    }
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

    private func observeCompletion(of item: AVPlayerItem) {
        if let completionObserver { NotificationCenter.default.removeObserver(completionObserver) }
        completionObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                await self.handlePlaybackEnded(item)
            }
        }
    }

    private func observeFailure(of item: AVPlayerItem) {
        if let failureObserver { NotificationCenter.default.removeObserver(failureObserver) }
        if let stalledObserver { NotificationCenter.default.removeObserver(stalledObserver) }
        failureObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor in
                let error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
                guard let self else { return }
                guard self.player.currentItem === item else { return }
                if self.itemHasReachedEnd(item) {
                    await self.handlePlaybackEnded(item)
                    return
                }
                if !self.playFromFallbackIfAvailable() {
                    await self.recoverFromPlaybackFailure(
                        error?.localizedDescription
                            ?? "This song could not be played."
                    )
                }
            }
        }
        stalledObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemPlaybackStalled,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.player.currentItem === item else { return }
                self.isBuffering = true
                self.playbackState = .loading
                self.schedulePlaybackWatchdog(for: item)
            }
        }
    }

    private func observeStatus(of item: AVPlayerItem) {
        Task { @MainActor [weak self, weak item] in
            guard let self, let item else { return }
            do {
                _ = try await item.asset.load(.isPlayable)
                guard self.player.currentItem === item else { return }
                if self.duration <= 0, let assetDuration = try? await item.asset.load(.duration) {
                    let seconds = CMTimeGetSeconds(assetDuration)
                    if seconds.isFinite, seconds > 0 {
                        self.duration = seconds
                        self.fallbackDuration = seconds
                    }
                }
                self.synchronizePlaybackState()
                self.playbackError = nil
                self.updateNowPlaying()
            } catch {
                guard self.player.currentItem === item else { return }
                if !self.playFromFallbackIfAvailable() {
                    await self.recoverFromPlaybackFailure(
                        error.localizedDescription
                    )
                }
            }
        }
    }

    private func playFromFallbackIfAvailable() -> Bool {
        guard currentRadioStation == nil else { return false }
        guard !isUsingPlaybackFallback, let fallbackURL = playbackFallbackURL else { return false }
        isUsingPlaybackFallback = true
        let fallbackSource = PlaybackSource(
            url: fallbackURL,
            durationSeconds: fallbackDuration
        )
        activePlaybackMetric?.fallbackUsed = true
        activePlaybackMetric?.sourceKind = .fallbackProxy
        let preparation: PreparedPlayback
        if let currentTrack {
            preparation = makePreparation(
                track: currentTrack,
                source: fallbackSource,
                preferredSourceKind: .fallbackProxy
            )
        } else {
            preparation = PreparedPlayback(
                source: fallbackSource,
                item: AVPlayerItem(url: fallbackURL),
                cachedAsset: nil,
                sourceKind: .fallbackProxy
            )
        }
        let item = preparation.item
        item.preferredForwardBufferDuration = 4
        completedItem = nil
        replacePlaybackItem(with: item)
        activeCachedAsset = preparation.cachedAsset
        observeCompletion(of: item)
        observeFailure(of: item)
        observeStatus(of: item)
        player.playImmediately(atRate: 1)
        schedulePlaybackWatchdog(for: item)
        return true
    }

    private func schedulePlaybackWatchdog(for item: AVPlayerItem) {
        playbackWatchdogTask?.cancel()
        playbackWatchdogTask = Task { @MainActor [weak self, weak item] in
            try? await Task.sleep(for: .seconds(8))
            guard !Task.isCancelled, let self, let item else { return }
            guard self.player.currentItem === item else { return }
            guard self.player.timeControlStatus != .playing else { return }
            guard self.playbackState != .paused else { return }
            if !self.playFromFallbackIfAvailable() {
                await self.recoverFromPlaybackFailure(
                    "This song took too long to start."
                )
            }
        }
    }

    private func synchronizePlaybackState() {
        switch player.timeControlStatus {
        case .playing:
            playbackWatchdogTask?.cancel()
            isPlaying = true
            isBuffering = false
            playbackState = .playing
            consecutivePlaybackFailures = 0
            if let playbackStartedAt {
                let elapsed = playbackStartedAt.duration(to: .now)
                playbackLogger.info("Playback audible in \(elapsed, privacy: .public)")
            }
            finishPlaybackMetric(success: true)
        case .waitingToPlayAtSpecifiedRate:
            isPlaying = false
            isBuffering = true
            playbackState = .loading
        case .paused:
            isPlaying = false
            isBuffering = false
            if currentTrack != nil, playbackError == nil { playbackState = .paused }
        @unknown default:
            break
        }
    }

    private func setPlaybackFailure(_ message: String) {
        playbackWatchdogTask?.cancel()
        player.pause()
        isPlaying = false
        isBuffering = false
        playbackError = message
        playbackState = .failed(message)
        updateNowPlaying()
        finishPlaybackMetric(success: false)
    }

    private func recoverFromPlaybackFailure(_ message: String) async {
        guard currentRadioStation == nil else {
            setPlaybackFailure(message)
            return
        }
        guard !isRecoveringPlaybackFailure else { return }
        isRecoveringPlaybackFailure = true
        defer { isRecoveringPlaybackFailure = false }
        finishPlaybackMetric(success: false)
        player.pause()
        isPlaying = false
        isBuffering = true
        playbackError = nil
        playbackState = .loading
        activeCachedAsset = nil
        if let trackID = currentTrack?.id {
            preparedPlaybacks.removeValue(forKey: trackID)
            recentPlaybackSources.removeValue(forKey: trackID)
            recentPlaybackTrackIDs.removeAll { $0 == trackID }
        }
        consecutivePlaybackFailures += 1
        guard consecutivePlaybackFailures < Self.maximumConsecutivePlaybackFailures else {
            setPlaybackFailure(
                "Several songs could not be played. Please try again in a moment."
            )
            return
        }
        if queue.isEmpty {
            await extendQueue()
        }
        guard let next = queue.first else {
            setPlaybackFailure(message)
            return
        }
        let remaining = Array(queue.dropFirst())
        currentTrack = nil
        await start(next, remaining: remaining, scenario: .autoplay)
    }

    private func playbackFailureMessage(for error: Error) -> String {
        if consecutivePlaybackFailures >= Self.maximumConsecutivePlaybackFailures {
            return "Several songs could not be played. Please try again in a moment."
        }
        return error.localizedDescription
    }

    private func playbackMilliseconds() -> Int? {
        guard let startedAt = activePlaybackMetric?.startedAt else {
            return nil
        }
        let components = startedAt.duration(to: .now).components
        let milliseconds = Int(components.seconds * 1_000)
            + Int(components.attoseconds / 1_000_000_000_000_000)
        return max(0, milliseconds)
    }

    private func finishPlaybackMetric(success: Bool) {
        guard let metric = activePlaybackMetric else { return }
        let audibleMilliseconds = success ? playbackMilliseconds() : nil
        activePlaybackMetric = nil
        playbackStartedAt = nil
        guard let client = session?.client else { return }
        let appVersion = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? ""
        let build = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleVersion"
        ) as? String ?? ""
        let version = build.isEmpty ? appVersion : "\(appVersion) (\(build))"
        let device = UIDevice.current
        let osVersion = "\(device.systemName) \(device.systemVersion)"
        Task {
            try? await client.recordPlaybackMetric(
                eventID: metric.eventID,
                trackID: metric.trackID,
                scenario: metric.scenario.rawValue,
                sourceKind: metric.sourceKind.rawValue,
                sourceReadyMilliseconds: metric.sourceReadyMilliseconds,
                audibleMilliseconds: audibleMilliseconds,
                success: success,
                fallbackUsed: metric.fallbackUsed,
                appVersion: version,
                osVersion: osVersion
            )
        }
    }

    private func advanceQueue(scenario: PlaybackScenario) async {
        if queue.isEmpty {
            await extendQueue()
        }
        guard let next = queue.first else {
            isPlaying = false
            playbackState = .paused
            updateNowPlaying()
            return
        }
        let remaining = Array(queue.dropFirst())
        await start(next, remaining: remaining, scenario: scenario)
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
        let completedTrack = currentTrack
        let playedSeconds = Int(elapsed)

        if let completedTrack, let client = session?.client {
            Task {
                try? await client.record(
                    completedTrack,
                    playedSeconds: playedSeconds,
                    completed: true
                )
            }
        }
        await advanceQueue(scenario: .autoplay)
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
        if self.duration > 0 {
            return self.duration
        }
        let seconds = CMTimeGetSeconds(item.duration)
        return (seconds.isFinite && seconds > 0) ? seconds : 0
    }

    private func validDuration(_ value: Double) -> Double? {
        guard value.isFinite, value > 0, value <= 24 * 60 * 60 else {
            return nil
        }
        return value
    }

    private func extendQueue() async {
        guard autoplayEnabled, !isExtendingQueue, let client = session?.client, let currentTrack else { return }
        isExtendingQueue = true
        defer { isExtendingQueue = false }
        let contextSeeds = sourceTrackIDs.isEmpty ? [currentTrack.id] : sourceTrackIDs
        let seeds = Array((contextSeeds + [currentTrack.id]).uniqued().suffix(3))
        let excluded = Array(Set(sourceTrackIDs).union(playedTrackIDs).union(queue.map(\.id)))
        do {
            let suggestions = try await client.recommendations(
                seedIDs: seeds,
                excluding: excluded,
                limit: 15
            )
            queue.append(contentsOf: suggestions.filter { suggestion in
                !playedTrackIDs.contains(suggestion.id) && !queue.contains { $0.id == suggestion.id }
            })
        } catch {
            playbackError = "Could not load more recommendations."
        }
    }

    private func remainingTracks(after track: Track, in tracks: [Track]) -> [Track] {
        guard let index = tracks.firstIndex(where: { $0.id == track.id }) else { return [] }
        return Array(tracks.dropFirst(index + 1))
    }

    private func recordHistoryIfNeeded() {
        guard elapsed >= 30, historyRecordedForTrack != currentTrack?.id else { return }
        historyRecordedForTrack = currentTrack?.id
        Task { await recordHistory(completed: false) }
    }

    private func recordHistory(completed: Bool) async {
        guard let track = currentTrack, let client = session?.client else { return }
        try? await client.record(track, playedSeconds: Int(elapsed), completed: completed)
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
        if let artwork { info[MPMediaItemPropertyArtwork] = artwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func prepareArtwork(for track: Track) {
        let highResURL = track.thumbnail.highResolutionMusicArtworkURL
        let listURL = track.thumbnail.musicArtworkURL(maximumDimension: 120) ?? URL(string: track.thumbnail)
        let highResCacheKey = (highResURL?.absoluteString ?? track.thumbnail) as NSString

        if let cached = Self.artworkCache.object(forKey: highResCacheKey) {
            artworkImage = cached
            updateNowPlaying(artwork: nowPlayingArtwork(from: cached))
            return
        }

        Task { @MainActor [weak self] in
            guard let self, self.currentTrack?.id == track.id else { return }
            if let listURL, let quickImage = await ArtworkCacheStore.shared.cachedImage(for: listURL) {
                if self.artworkImage == nil {
                    self.artworkImage = quickImage
                    self.updateNowPlaying(artwork: self.nowPlayingArtwork(from: quickImage))
                }
            }
        }

        guard let url = highResURL else { return }
        Task {
            guard let image = await ArtworkCacheStore.shared.image(for: url),
                  currentTrack?.id == track.id else { return }
            Self.artworkCache.setObject(image, forKey: highResCacheKey)
            await MainActor.run { [weak self] in
                guard let self, self.currentTrack?.id == track.id else { return }
                self.artworkImage = image
                self.updateNowPlaying(artwork: self.nowPlayingArtwork(from: image))
            }
        }
    }

    private func nowPlayingArtwork(from image: UIImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: image.size) { _ in image }
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
