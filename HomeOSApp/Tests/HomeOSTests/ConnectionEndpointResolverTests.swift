@testable import HomeOS
import XCTest

final class ConnectionEndpointResolverTests: XCTestCase {
    func testCandidatesPreferLocalBeforeDomain() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk/",
            localURL: " http://192.168.1.20:5000/ ",
            preferLocal: true
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "http://192.168.1.20:5000"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ])
    }

    func testCandidatesPreferDomainBeforeLocalWhenLocalPreferenceDisabled() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "http://192.168.1.20:5000",
            preferLocal: false
        )

        XCTAssertEqual(candidates.map(\.kind), [.remote, .local])
    }

    func testCandidatesIncludeDiscoveredLocalURLsBeforeDomain() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "",
            discoveredLocalURLs: ["http://homeos.local:5000", "http://192.168.1.20:5000"],
            preferLocal: true
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "http://homeos.local:5000"),
            ConnectionEndpoint(kind: .local, url: "http://192.168.1.20:5000"),
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
            ConnectionEndpoint(kind: .local, url: "http://192.168.1.20:5000"),
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
            " http://homeos.local:5000/ ",
            "ftp://homeos.local",
            "http://homeos.local:5000",
        ])

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "http://homeos.local:5000"),
        ])
    }

    func testResolveReportsAllFailures() async {
        let resolver = ConnectionEndpointResolver()
        let candidates = [
            ConnectionEndpoint(kind: .local, url: "http://192.168.1.20:5000"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ]

        let result = await resolver.resolve(candidates: candidates) { _ in false }

        XCTAssertNil(result.endpoint)
        XCTAssertEqual(result.failures.count, 2)
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
