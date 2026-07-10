import Foundation
@testable import HomeOS
import XCTest

final class APIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        MockURLProtocol.lastRequest = nil
        super.tearDown()
    }

    func testClientSendsBrowserLikeHeadersAndBearerToken() async throws {
        let session = makeMockSession()
        let client = try APIClient(baseURL: "https://example.test", authToken: "test-token", session: session)

        MockURLProtocol.requestHandler = { request in
            let body = #"{"ok":true,"data":{"cpu_percent":1,"cpu_count":4,"memory":{"total_gb":8,"used_gb":2,"percent":25},"disk":{"total_gb":100,"used_gb":20,"percent":20},"network":{"sent_gb":0.1,"recv_gb":0.2},"uptime":"1d 0h","hostname":"test"}}"#.data(using: .utf8)!
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            return (response, body)
        }

        let response = try await client.getMetrics()

        XCTAssertTrue(response.ok)
        let request = try XCTUnwrap(MockURLProtocol.lastRequest)
        XCTAssertEqual(request.url?.path, "/api/monitor/metrics")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Requested-With"), "XMLHttpRequest")
        XCTAssertTrue(request.value(forHTTPHeaderField: "User-Agent")?.contains("HomeOSMac/1.0") == true)
    }

    func testHTTPErrorIncludesStatusAndBodySnippet() async throws {
        let session = makeMockSession()
        let client = try APIClient(baseURL: "https://example.test", session: session)

        MockURLProtocol.requestHandler = { request in
            let body = "Cloudflare blocked this client".data(using: .utf8)!
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 403,
                httpVersion: nil,
                headerFields: ["Content-Type": "text/plain"]
            )!
            return (response, body)
        }

        do {
            _ = try await client.getMetrics()
            XCTFail("Expected request to fail")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("HTTP 403"))
            XCTAssertTrue(error.localizedDescription.contains("Cloudflare blocked"))
        }
    }

    func testConnectionInfoUsesExpectedAPIPath() async throws {
        let session = makeMockSession()
        let client = try APIClient(baseURL: "https://example.test", authToken: "test-token", session: session)

        MockURLProtocol.requestHandler = { request in
            let body = #"{"ok":true,"data":{"hostname":"homeos","port":5000,"local_urls":["http://homeos.local:5000"]}}"#.data(using: .utf8)!
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            return (response, body)
        }

        let response = try await client.getConnectionInfo()

        XCTAssertTrue(response.ok)
        let request = try XCTUnwrap(MockURLProtocol.lastRequest)
        XCTAssertEqual(request.url?.path, "/api/network/connection-info")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
    }

    func testHealthUsesExpectedPublicPath() async throws {
        let session = makeMockSession()
        let client = try APIClient(baseURL: "https://example.test", session: session)

        MockURLProtocol.requestHandler = { request in
            let body = #"{"status":"healthy","version":"0.1.0"}"#.data(using: .utf8)!
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            return (response, body)
        }

        let response = try await client.getHealth()

        XCTAssertEqual(response.status, "healthy")
        XCTAssertEqual(response.version, "0.1.0")
        let request = try XCTUnwrap(MockURLProtocol.lastRequest)
        XCTAssertEqual(request.url?.path, "/health")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
    }

    func testFileOperationsPostExpectedPayloads() async throws {
        let session = makeMockSession()
        let client = try APIClient(baseURL: "https://example.test", authToken: "test-token", session: session)
        var requests: [(path: String, body: [String: String])] = []

        MockURLProtocol.requestHandler = { request in
            let data = request.httpBodyStreamData ?? Data()
            let body = (try? JSONSerialization.jsonObject(with: data) as? [String: String]) ?? [:]
            requests.append((request.url?.path ?? "", body))
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            return (response, #"{"ok":true}"#.data(using: .utf8)!)
        }

        _ = try await client.rename(path: "/old.txt", newName: "new.txt")
        _ = try await client.copy(sourcePath: "/new.txt", destinationPath: "/Archive")
        _ = try await client.move(sourcePath: "/new.txt", destinationPath: "/Photos")
        _ = try await client.delete(path: "/new.txt")

        XCTAssertEqual(requests.map(\.path), [
            "/api/files/rename",
            "/api/files/copy",
            "/api/files/move",
            "/api/files/delete",
        ])
        XCTAssertEqual(requests[0].body, ["path": "/old.txt", "new_name": "new.txt"])
        XCTAssertEqual(requests[1].body, ["src": "/new.txt", "dest": "/Archive"])
        XCTAssertEqual(requests[2].body, ["src": "/new.txt", "dest": "/Photos"])
        XCTAssertEqual(requests[3].body, ["path": "/new.txt"])
    }

    private func makeMockSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
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

private final class MockURLProtocol: URLProtocol {
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
