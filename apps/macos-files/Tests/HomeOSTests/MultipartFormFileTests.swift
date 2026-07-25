import Foundation
@testable import HomeOS
import XCTest

final class MultipartFormFileTests: XCTestCase {
    func testMultipartBodyIsWrittenToDiskWithExpectedContent() throws {
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("multipart-source-\(UUID().uuidString).txt")
        try Data("streamed-file-contents".utf8).write(to: source)
        defer { try? FileManager.default.removeItem(at: source) }

        let body = try MultipartFormFile.create(
            destinationPath: "/Documents",
            fileURL: source,
            requestedFilename: "report.txt",
            boundary: "TestBoundary"
        )
        defer { try? FileManager.default.removeItem(at: body.url) }

        let data = try Data(contentsOf: body.url)
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))
        XCTAssertEqual(body.contentLength, Int64(data.count))
        XCTAssertTrue(text.contains("name=\"path\"\r\n\r\n/Documents"))
        XCTAssertTrue(text.contains("filename=\"report.txt\""))
        XCTAssertTrue(text.contains("streamed-file-contents"))
        XCTAssertTrue(text.hasSuffix("\r\n--TestBoundary--\r\n"))
    }

    func testMultipartFilenameRemovesHeaderBreakingCharacters() throws {
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("multipart-source-\(UUID().uuidString)")
        try Data("content".utf8).write(to: source)
        defer { try? FileManager.default.removeItem(at: source) }

        let body = try MultipartFormFile.create(
            destinationPath: "/",
            fileURL: source,
            requestedFilename: "bad\"\r\nname.txt",
            boundary: "Boundary"
        )
        defer { try? FileManager.default.removeItem(at: body.url) }

        let text = try String(contentsOf: body.url, encoding: .utf8)
        XCTAssertTrue(text.contains("filename=\"bad___name.txt\""))
    }
}
