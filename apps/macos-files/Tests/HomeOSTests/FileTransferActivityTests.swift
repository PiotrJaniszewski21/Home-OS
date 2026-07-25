@testable import HomeOS
import XCTest

final class FileTransferActivityTests: XCTestCase {
    func testProgressIsClampedForMenuBarPresentation() {
        let overComplete = FileTransferActivity(
            id: UUID(),
            filename: "large-video.mov",
            kind: .upload,
            fractionCompleted: 1.2
        )
        let belowZero = FileTransferActivity(
            id: UUID(),
            filename: "archive.zip",
            kind: .download,
            fractionCompleted: -0.1
        )

        XCTAssertEqual(overComplete.clampedFractionCompleted, 1)
        XCTAssertEqual(belowZero.clampedFractionCompleted, 0)
        XCTAssertEqual(overComplete.kind.title, "Uploading")
        XCTAssertEqual(belowZero.kind.title, "Downloading")
    }

    @MainActor
    func testTransferMonitorTracksBridgeEvents() {
        let monitor = FileTransferActivityMonitor()
        let identifier = UUID()

        monitor.receive(
            HomeOSTransferProgressBridge.Event(
                identifier: identifier,
                kind: .upload,
                filename: "large-video.mov",
                fractionCompleted: 0.42,
                phase: .updated
            )
        )

        XCTAssertEqual(monitor.activities.count, 1)
        XCTAssertEqual(monitor.activities.first?.fractionCompleted, 0.42)

        let downloadIdentifier = UUID()
        monitor.receive(
            HomeOSTransferProgressBridge.Event(
                identifier: downloadIdentifier,
                kind: .download,
                filename: "server-video.mkv",
                fractionCompleted: 0.25,
                phase: .updated
            )
        )

        XCTAssertEqual(monitor.activities.count, 2)
        XCTAssertEqual(Set(monitor.activities.map(\.kind)), Set([.upload, .download]))

        monitor.receive(
            HomeOSTransferProgressBridge.Event(
                identifier: identifier,
                kind: .upload,
                filename: "large-video.mov",
                fractionCompleted: 1,
                phase: .finished
            )
        )

        XCTAssertEqual(monitor.activities.count, 1)
        XCTAssertEqual(monitor.activities.first?.kind, .download)
        monitor.stop()
    }
}
