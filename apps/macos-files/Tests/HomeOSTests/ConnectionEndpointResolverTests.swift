@testable import HomeOS
import XCTest

final class ConnectionEndpointResolverTests: XCTestCase {
    func testCandidatesPreferLocalBeforeDomain() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk/",
            localURL: " https://192.168.1.20:4443/ ",
            preferLocal: true
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20:4443"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ])
    }

    func testCandidatesPreferDomainBeforeLocalWhenLocalPreferenceDisabled() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "https://192.168.1.20:4443",
            preferLocal: false
        )

        XCTAssertEqual(candidates.map(\.kind), [.remote, .local])
    }

    func testCandidatesIncludeDiscoveredLocalURLsBeforeDomain() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "",
            discoveredLocalURLs: ["https://homeos.local:4443", "https://192.168.1.20:4443"],
            preferLocal: true
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "https://homeos.local:4443"),
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20:4443"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ])
    }

    func testCandidatesDeduplicateMatchingURLs() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk/",
            localURL: "https://petershomenet.co.uk",
            preferLocal: true
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "https://petershomenet.co.uk"),
        ])
    }

    func testResolveFallsBackToDomainWhenLocalProbeFails() async {
        let resolver = ConnectionEndpointResolver()
        let recorder = EndpointProbeRecorder()
        let candidates = [
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20:4443"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ]

        let result = await resolver.resolve(candidates: candidates) { endpoint in
            await recorder.append(endpoint.kind)
            return endpoint.kind == .remote
        }

        let probedKinds = await recorder.values()
        XCTAssertEqual(result.endpoint, candidates[1])
        XCTAssertEqual(probedKinds, [.local, .remote])
        XCTAssertEqual(result.failures, [
            EndpointResolutionFailure(endpoint: candidates[0], message: "Server did not report healthy."),
        ])
    }

    func testLocalCandidatesNormalizeAndDeduplicateURLs() {
        let candidates = ConnectionEndpointResolver.localCandidates([
            " https://homeos.local:4443/ ",
            "ftp://homeos.local",
            "https://homeos.local:4443",
        ])

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "https://homeos.local:4443"),
        ])
    }

    func testResolveReportsAllFailures() async {
        let resolver = ConnectionEndpointResolver()
        let candidates = [
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20:4443"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ]

        let result = await resolver.resolve(candidates: candidates) { _ in false }

        XCTAssertNil(result.endpoint)
        XCTAssertEqual(result.failures.count, 2)
    }

    func testRejectsPlainHTTP() {
        XCTAssertNil(ConnectionEndpointResolver.normalizedURL("http://192.168.1.20:5000"))
    }
}

private actor EndpointProbeRecorder {
    private var probedKinds: [ConnectionEndpoint.Kind] = []

    func append(_ kind: ConnectionEndpoint.Kind) {
        probedKinds.append(kind)
    }

    func values() -> [ConnectionEndpoint.Kind] {
        probedKinds
    }
}
