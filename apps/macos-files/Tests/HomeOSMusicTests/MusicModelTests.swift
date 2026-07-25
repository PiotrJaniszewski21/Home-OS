import Foundation
@testable import HomeOSMusic
import XCTest

final class MusicModelTests: XCTestCase {
    func testMusicTrackDecodesBackendShape() throws {
        let data = #"""
        {
          "id": "song-1",
          "title": "Example",
          "artist": "Example Artist",
          "artist_id": "artist-1",
          "thumbnail": "https://img.test/cover=w120-h120",
          "duration": "3:10",
          "duration_seconds": 190,
          "explicit": true,
          "play_count": 4,
          "liked": true,
          "last_played_at": "2026-07-15T12:00:00Z"
        }
        """#.data(using: .utf8)!

        let track = try JSONDecoder().decode(MusicTrack.self, from: data)

        XCTAssertEqual(track.id, "song-1")
        XCTAssertEqual(track.artistID, "artist-1")
        XCTAssertEqual(track.durationSeconds, 190)
        XCTAssertEqual(track.playCount, 4)
        XCTAssertEqual(track.liked, true)
    }

    func testAlbumArtworkFillsMissingTrackArtwork() {
        let track = MusicTrack(
            id: "song-1",
            title: "Example",
            artist: "Artist",
            artistID: nil,
            thumbnail: "",
            duration: nil,
            durationSeconds: 190,
            explicit: nil,
            playCount: nil,
            liked: nil,
            lastPlayedAt: nil
        )

        let resolved = track.usingFallbackArtwork("https://img.test/album.jpg")

        XCTAssertEqual(resolved.thumbnail, "https://img.test/album.jpg")
        XCTAssertEqual(resolved.id, track.id)
    }

    func testHighResolutionArtworkPreservesAspectRatio() {
        let source = "https://img.test/art=w320-h180-l90-rj"

        let result = source.highResolutionMusicArtworkURL?.absoluteString

        XCTAssertEqual(result, "https://img.test/art=w1200-h675-l90-rj")
    }

    func testRadioStationCreatesLivePlayerTrack() {
        let station = MusicRadioStation(
            id: "capital",
            name: "Capital",
            streamURL: "https://example.test/radio",
            artwork: "https://img.test/capital.jpg",
            country: "United Kingdom",
            countryCode: "GB",
            language: "English",
            tags: ["pop", "hits"],
            codec: "MP3",
            bitrate: 128,
            isHLS: false
        )

        XCTAssertEqual(station.subtitle, "Pop · Hits")
        XCTAssertEqual(station.playerTrack.title, "Capital")
        XCTAssertNil(station.playerTrack.durationSeconds)
    }

    func testRepeatModeCyclesThroughEveryMode() {
        XCTAssertEqual(MusicRepeatMode.off.next, .all)
        XCTAssertEqual(MusicRepeatMode.all.next, .one)
        XCTAssertEqual(MusicRepeatMode.one.next, .off)
    }
}
