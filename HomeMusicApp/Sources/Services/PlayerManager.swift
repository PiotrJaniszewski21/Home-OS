import AVFoundation
import MediaPlayer
import UIKit

@MainActor
final class PlayerManager: ObservableObject {
    @Published private(set) var currentTrack: Track?
    @Published private(set) var isPlaying = false
    @Published private(set) var elapsed: Double = 0
    @Published private(set) var duration: Double = 0
    @Published var queue: [Track] = []
    private var previousTracks: [Track] = []

    private let player = AVPlayer()
    private var timeObserver: Any?
    private var completionObserver: NSObjectProtocol?
    private var historyRecordedForTrack: String?
    private weak var session: AppSession?

    init() {
        configureAudioSession()
        configureRemoteCommands()
        observeTime()
    }

    deinit {
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        if let completionObserver { NotificationCenter.default.removeObserver(completionObserver) }
    }

    func connect(session: AppSession) {
        self.session = session
    }

    func play(_ track: Track, from tracks: [Track] = []) async {
        guard let client = session?.client else { return }
        do {
            let url = try await client.playbackURL(for: track)
            if let currentTrack, currentTrack.id != track.id { previousTracks.append(currentTrack) }
            currentTrack = track
            queue = tracks.filter { $0.id != track.id }
            historyRecordedForTrack = nil
            elapsed = 0
            duration = Double(track.durationSeconds ?? 0)
            let item = AVPlayerItem(url: url)
            player.replaceCurrentItem(with: item)
            observeCompletion(of: item)
            player.play()
            isPlaying = true
            updateNowPlaying()
            loadArtwork(for: track)
        } catch {
            isPlaying = false
        }
    }

    func togglePlayback() {
        guard currentTrack != nil else { return }
        if isPlaying { player.pause() } else { player.play() }
        isPlaying.toggle()
        updateNowPlaying()
    }

    func seek(to value: Double) {
        player.seek(to: CMTime(seconds: value, preferredTimescale: 600))
        elapsed = value
        updateNowPlaying()
    }

    func playNext() {
        guard let next = queue.first else { return }
        queue.removeFirst()
        Task { await play(next, from: queue) }
    }

    func playPrevious() {
        guard let previous = previousTracks.popLast() else {
            seek(to: 0)
            return
        }
        Task { await play(previous, from: queue) }
    }

    func toggleLike() async {
        guard let track = currentTrack, let client = session?.client else { return }
        if let updated = try? await client.setLiked(!(track.liked ?? false), track: track) {
            currentTrack = updated
            updateNowPlaying()
        }
    }

    private func configureAudioSession() {
        do {
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.playback, mode: .default, options: [.allowAirPlay])
            try audio.setActive(true)
        } catch {}
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
                self.elapsed = max(0, time.seconds.isFinite ? time.seconds : 0)
                let itemDuration = self.player.currentItem?.duration.seconds ?? 0
                if itemDuration.isFinite { self.duration = max(self.duration, itemDuration) }
                self.updateNowPlaying()
                self.recordHistoryIfNeeded()
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
                await self.recordHistory(completed: true)
                self.playNext()
            }
        }
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
        info[MPMediaItemPropertyPlaybackDuration] = duration
        info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = elapsed
        info[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? 1.0 : 0.0
        info[MPNowPlayingInfoPropertyExternalContentIdentifier] = track.id
        if let artwork { info[MPMediaItemPropertyArtwork] = artwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func loadArtwork(for track: Track) {
        guard let url = URL(string: track.thumbnail) else { return }
        Task {
            guard let (data, _) = try? await URLSession.shared.data(from: url),
                  let image = UIImage(data: data), currentTrack?.id == track.id else { return }
            let artwork = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
            updateNowPlaying(artwork: artwork)
        }
    }
}
