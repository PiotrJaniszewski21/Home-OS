import Foundation
@testable import HomeOSMusic
import XCTest

final class MusicAPIClientTests: XCTestCase {
    override func tearDown() {
        MusicMockURLProtocol.requestHandler = nil
        MusicMockURLProtocol.lastRequest = nil
        super.tearDown()
    }

    func testMusicSearchUsesAuthenticatedEndpoint() async throws {
        let session = makeMockSession()
        let client = try APIClient(
            baseURL: "https://example.test",
            authToken: "music-token",
            session: session
        )

        MusicMockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            let body = #"{"ok":true,"data":[{"id":"track-1","title":"Song","artist":"Artist","artist_id":"artist-1","thumbnail":"https://img.test/cover=w120-h120","duration":"3:20","duration_seconds":200,"explicit":false}]}"#.data(using: .utf8)!
            return (response, body)
        }

        let tracks = try await client.musicSearch("Song & Artist")

        XCTAssertEqual(tracks.first?.id, "track-1")
        let request = try XCTUnwrap(MusicMockURLProtocol.lastRequest)
        XCTAssertEqual(request.url?.path, "/api/music/search")
        XCTAssertEqual(
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "q" })?.value,
            "Song & Artist"
        )
        XCTAssertEqual(
            request.value(forHTTPHeaderField: "Authorization"),
            "Bearer music-token"
        )
    }

    func testMusicPlaybackPreservesProxyTicketQuery() async throws {
        let session = makeMockSession()
        let client = try APIClient(
            baseURL: "https://example.test/base",
            authToken: "music-token",
            session: session
        )
        let track = MusicTrack(
            id: "track-1",
            title: "Song",
            artist: "Artist",
            artistID: nil,
            thumbnail: "",
            duration: nil,
            durationSeconds: nil,
            explicit: nil,
            playCount: nil,
            liked: nil,
            lastPlayedAt: nil
        )

        MusicMockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            let body = #"{"ok":true,"data":{"path":"/proxy-stream?id=track-1&ticket=signed-ticket","direct_url":null,"expires_in":300,"duration_seconds":180.5}}"#.data(using: .utf8)!
            return (response, body)
        }

        let source = try await client.musicPlayback(for: track)

        XCTAssertEqual(source.url.path, "/proxy-stream")
        let items = URLComponents(
            url: source.url,
            resolvingAgainstBaseURL: false
        )?.queryItems
        XCTAssertEqual(items?.first(where: { $0.name == "id" })?.value, "track-1")
        XCTAssertEqual(
            items?.first(where: { $0.name == "ticket" })?.value,
            "signed-ticket"
        )
        XCTAssertEqual(source.durationSeconds, 180.5)
    }

    func testMusicPlaylistAddSendsTrackMetadata() async throws {
        let session = makeMockSession()
        let client = try APIClient(
            baseURL: "https://example.test",
            authToken: "music-token",
            session: session
        )
        let track = MusicTrack(
            id: "track-1",
            title: "Song",
            artist: "Artist",
            artistID: nil,
            thumbnail: "https://img.test/song.jpg",
            duration: "3:20",
            durationSeconds: 200,
            explicit: false,
            playCount: nil,
            liked: false,
            lastPlayedAt: nil
        )
        let playlist = MusicPlaylist(
            id: 9,
            name: "Mix",
            description: "",
            trackCount: 0,
            artwork: [],
            tracks: [],
            createdAt: "2026-07-15T12:00:00Z",
            updatedAt: "2026-07-15T12:00:00Z"
        )

        MusicMockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            let body = #"{"ok":true,"data":{"id":9,"name":"Mix","description":"","track_count":1,"artwork":["https://img.test/song.jpg"],"tracks":[{"id":"track-1","title":"Song","artist":"Artist","thumbnail":"https://img.test/song.jpg","duration":"3:20","duration_seconds":200}],"created_at":"2026-07-15T12:00:00Z","updated_at":"2026-07-15T12:01:00Z"}}"#.data(using: .utf8)!
            return (response, body)
        }

        let updated = try await client.musicAdd(track, to: playlist)

        XCTAssertEqual(updated.trackCount, 1)
        let request = try XCTUnwrap(MusicMockURLProtocol.lastRequest)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/api/music/playlists/9/tracks")
        let body = try XCTUnwrap(request.httpBodyStreamData)
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        XCTAssertEqual(payload["id"] as? String, "track-1")
        XCTAssertEqual(payload["duration_seconds"] as? Int, 200)
    }

    private func makeMockSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MusicMockURLProtocol.self]
        return URLSession(configuration: configuration)
    }
}

private extension URLRequest {
    var httpBodyStreamData: Data? {
        guard let stream = httpBodyStream else { return httpBody }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 1024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let count = stream.read(buffer, maxLength: bufferSize)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

private final class MusicMockURLProtocol: URLProtocol {
    nonisolated(unsafe) static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        Self.lastRequest = request
        guard let handler = Self.requestHandler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
