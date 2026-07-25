import FileProvider
import XCTest
@testable import HomeOS

@MainActor
final class FileProviderDomainServiceTests: XCTestCase {
    func testRemoteRefreshIncludesCanonicalUserFolder() {
        let identifiers = FileProviderDomainService.remoteChangeContainerIdentifiers(
            username: "peter",
            userDirectoryEntries: [
                entry(name: "Peter", path: "/users/Peter"),
            ]
        )

        XCTAssertEqual(
            identifiers.map(\.rawValue),
            [
                NSFileProviderItemIdentifier.rootContainer.rawValue,
                NSFileProviderItemIdentifier.workingSet.rawValue,
                "/users",
                "/users/Peter",
            ]
        )
    }

    func testRemoteRefreshDoesNotSignalAnotherUsersFolder() {
        let identifiers = FileProviderDomainService.remoteChangeContainerIdentifiers(
            username: "peter",
            userDirectoryEntries: [
                entry(name: "Alex", path: "/users/Alex"),
            ]
        )

        XCTAssertEqual(
            identifiers.map(\.rawValue),
            [
                NSFileProviderItemIdentifier.rootContainer.rawValue,
                NSFileProviderItemIdentifier.workingSet.rawValue,
                "/users",
                "/users/peter",
            ]
        )
    }

    func testRemoteRefreshFallsBackToAuthenticatedUsername() {
        let identifiers = FileProviderDomainService.remoteChangeContainerIdentifiers(
            username: "Peter",
            userDirectoryEntries: []
        )

        XCTAssertEqual(identifiers.last?.rawValue, "/users/Peter")
    }

    func testRemoteRefreshIgnoresMatchingFiles() {
        let identifiers = FileProviderDomainService.remoteChangeContainerIdentifiers(
            username: "Peter",
            userDirectoryEntries: [
                entry(name: "Peter", path: "/users/not-a-folder", isDirectory: false),
            ]
        )

        XCTAssertFalse(identifiers.map(\.rawValue).contains("/users/not-a-folder"))
        XCTAssertEqual(identifiers.last?.rawValue, "/users/Peter")
    }

    private func entry(name: String, path: String, isDirectory: Bool = true) -> FileEntry {
        FileEntry(
            name: name,
            path: path,
            isDirectory: isDirectory,
            size: nil,
            modified: nil,
            extensionType: nil
        )
    }
}
