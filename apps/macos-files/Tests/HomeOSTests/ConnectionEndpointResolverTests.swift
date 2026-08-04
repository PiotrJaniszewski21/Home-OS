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
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ])
    }

    func testCandidatesPreferDomainBeforeLocalWhenLocalPreferenceDisabled() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "https://192.168.1.20",
            preferLocal: false
        )

        XCTAssertEqual(candidates.map(\.kind), [.remote, .local])
    }

    func testFileProviderCandidatesPreferDomainWithLocalFallback() {
        let candidates = ConnectionEndpointResolver.fileProviderCandidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "https://192.168.1.20:4443"
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20"),
        ])
    }

    func testCandidatesIncludeDiscoveredLocalURLsBeforeDomain() {
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: "https://petershomenet.co.uk",
            localURL: "",
            discoveredLocalURLs: ["https://homeos.local", "https://192.168.1.20"],
            preferLocal: true
        )

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "https://homeos.local"),
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20"),
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
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20"),
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
            "https://homeos.local",
        ])

        XCTAssertEqual(candidates, [
            ConnectionEndpoint(kind: .local, url: "https://homeos.local"),
        ])
    }

    func testResolveReportsAllFailures() async {
        let resolver = ConnectionEndpointResolver()
        let candidates = [
            ConnectionEndpoint(kind: .local, url: "https://192.168.1.20"),
            ConnectionEndpoint(kind: .remote, url: "https://petershomenet.co.uk"),
        ]

        let result = await resolver.resolve(candidates: candidates) { _ in false }

        XCTAssertNil(result.endpoint)
        XCTAssertEqual(result.failures.count, 2)
    }

    func testRejectsPlainHTTP() {
        XCTAssertNil(ConnectionEndpointResolver.normalizedURL("http://192.168.1.20:5000"))
    }

    func testMigratesLegacyPortAndPreservesOtherExplicitPorts() {
        XCTAssertEqual(
            ConnectionEndpointResolver.normalizedURL("https://192.168.1.20:4443"),
            "https://192.168.1.20"
        )
        XCTAssertEqual(
            ConnectionEndpointResolver.normalizedURL("https://home.example:8443"),
            "https://home.example:8443"
        )
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
