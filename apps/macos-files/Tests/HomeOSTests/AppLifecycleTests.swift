@testable import HomeOS
import XCTest

final class AppLifecycleTests: XCTestCase {
    func testOnlyOldestHomeOSProcessContinuesRunning() {
        XCTAssertFalse(
            AppDelegate.shouldTerminateDuplicateInstance(
                currentPID: 100,
                runningPIDs: [100]
            )
        )
        XCTAssertFalse(
            AppDelegate.shouldTerminateDuplicateInstance(
                currentPID: 100,
                runningPIDs: [101, 100]
            )
        )
        XCTAssertTrue(
            AppDelegate.shouldTerminateDuplicateInstance(
                currentPID: 101,
                runningPIDs: [101, 100]
            )
        )
    }

    func testMissingRunningApplicationSnapshotDoesNotTerminateApp() {
        XCTAssertFalse(
            AppDelegate.shouldTerminateDuplicateInstance(
                currentPID: 100,
                runningPIDs: []
            )
        )
    }
}
