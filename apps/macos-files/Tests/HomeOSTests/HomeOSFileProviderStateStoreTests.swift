import FileProvider
import Foundation
@testable import HomeOS
import XCTest

final class HomeOSFileProviderStateStoreTests: XCTestCase {
    func testIdentityStorePreservesIdentifiersAcrossFolderRename() {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderIdentityStore(defaults: defaults)

        let folder = store.identifier(forNormalizedPath: "/Folder")
        let child = store.identifier(forNormalizedPath: "/Folder/child.txt")
        store.moveTree(
            fromNormalizedPath: "/Folder",
            toNormalizedPath: "/Renamed",
            rootIdentifier: folder
        )

        XCTAssertEqual(store.path(for: folder), "/Renamed")
        XCTAssertEqual(store.path(for: child), "/Renamed/child.txt")
        XCTAssertEqual(store.identifier(forNormalizedPath: "/Renamed"), folder)
        XCTAssertEqual(store.identifier(forNormalizedPath: "/Renamed/child.txt"), child)
    }

    func testIdentityStoreDoesNotReuseIdentifierWhenOriginalFolderNameReturns() {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderIdentityStore(defaults: defaults)

        let documents = store.identifier(forNormalizedPath: "/users/Peter/untitled folder")
        store.moveTree(
            fromNormalizedPath: "/users/Peter/untitled folder",
            toNormalizedPath: "/users/Peter/Documents",
            rootIdentifier: documents
        )
        let newUntitledFolder = store.identifier(
            forNormalizedPath: "/users/Peter/untitled folder"
        )

        XCTAssertNotEqual(newUntitledFolder, documents)
        XCTAssertEqual(store.path(for: documents), "/users/Peter/Documents")
        XCTAssertEqual(
            store.path(for: newUntitledFolder),
            "/users/Peter/untitled folder"
        )
    }

    func testIdentityStoreRepairsPersistedDuplicateIdentifiers() {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let reusedIdentifier = "/users/Peter/untitled folder"
        defaults.set(
            [
                "/users/Peter/3D Models": reusedIdentifier,
                "/users/Peter/Documents": reusedIdentifier,
                "/users/Peter/untitled folder": reusedIdentifier,
            ],
            forKey: "HomeOSFileProviderPathIdentifiers"
        )
        let store = HomeOSFileProviderIdentityStore(defaults: defaults)

        let models = store.identifier(forNormalizedPath: "/users/Peter/3D Models")
        let documents = store.identifier(forNormalizedPath: "/users/Peter/Documents")
        let untitled = store.identifier(forNormalizedPath: "/users/Peter/untitled folder")

        XCTAssertEqual(Set([models, documents, untitled]).count, 3)
        XCTAssertEqual(store.path(for: models), "/users/Peter/3D Models")
        XCTAssertEqual(store.path(for: documents), "/users/Peter/Documents")
        XCTAssertEqual(store.path(for: untitled), "/users/Peter/untitled folder")
    }

    func testIdentityStoreCanBeClearedBetweenAccounts() {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderIdentityStore(defaults: defaults)
        _ = store.identifier(forNormalizedPath: "/private.txt")
        store.clear()
        XCTAssertNil(defaults.dictionary(forKey: "HomeOSFileProviderPathIdentifiers"))
    }

    func testSnapshotStoreReportsUpdatesAndDeletions() throws {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderSnapshotStore(defaults: defaults)
        let original = [
            snapshot("/a.txt", fingerprint: "v1"),
            snapshot("/b.txt", fingerprint: "v1"),
        ]
        let anchor = store.recordFullEnumeration(containerIdentifier: "root", snapshots: original)

        let changes = try store.changes(
            containerIdentifier: "root",
            from: anchor,
            current: [
                snapshot("/a.txt", fingerprint: "v2"),
                snapshot("/c.txt", fingerprint: "v1"),
            ]
        )

        XCTAssertEqual(changes.updatedIdentifiers, ["/a.txt", "/c.txt"])
        XCTAssertEqual(changes.deletedIdentifiers, ["/b.txt"])
    }

    func testSnapshotHistoryAcceptsAnchorFromBeforeFullEnumeration() throws {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderSnapshotStore(defaults: defaults)
        let originalAnchor = store.recordFullEnumeration(
            containerIdentifier: "root",
            snapshots: [snapshot("/a.txt", fingerprint: "v1")]
        )
        _ = store.recordFullEnumeration(
            containerIdentifier: "root",
            snapshots: [snapshot("/a.txt", fingerprint: "v2")]
        )

        let changes = try store.changes(
            containerIdentifier: "root",
            from: originalAnchor,
            current: [snapshot("/a.txt", fingerprint: "v2")]
        )

        XCTAssertEqual(changes.updatedIdentifiers, ["/a.txt"])
        XCTAssertTrue(changes.deletedIdentifiers.isEmpty)
    }

    func testUnknownSnapshotAnchorExpires() {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderSnapshotStore(defaults: defaults)

        XCTAssertThrowsError(try store.changes(
            containerIdentifier: "root",
            from: Data("unknown".utf8),
            current: []
        )) { error in
            let nsError = error as NSError
            XCTAssertEqual(nsError.domain, NSFileProviderErrorDomain)
            XCTAssertEqual(nsError.code, NSFileProviderError.Code.syncAnchorExpired.rawValue)
        }
    }

    func testSnapshotStoreReportsForcedMetadataUpdate() throws {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderSnapshotStore(defaults: defaults)
        let unchanged = snapshot("/a.txt", fingerprint: "v1")
        let anchor = store.recordFullEnumeration(containerIdentifier: "root", snapshots: [unchanged])

        store.markUpdated(identifiers: ["/a.txt"], in: ["root"])
        XCTAssertEqual(store.pendingUpdatedIdentifiers(in: "root"), ["/a.txt"])
        let changes = try store.changes(
            containerIdentifier: "root",
            from: anchor,
            current: [unchanged]
        )

        XCTAssertEqual(changes.updatedIdentifiers, ["/a.txt"])
    }

    func testSnapshotStorePreservesItemsOutsideAuthoritativeParents() throws {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderSnapshotStore(defaults: defaults)
        let original = [
            snapshot("/root-folder", parent: "root", fingerprint: "v1"),
            snapshot("/nested.txt", parent: "/unscanned", fingerprint: "v1"),
        ]
        let anchor = store.recordFullEnumeration(containerIdentifier: "working-set", snapshots: original)

        let changes = try store.changes(
            containerIdentifier: "working-set",
            from: anchor,
            current: [snapshot("/root-folder", parent: "root", fingerprint: "v1")],
            authoritativeParentIdentifiers: ["root"]
        )

        XCTAssertTrue(changes.updatedIdentifiers.isEmpty)
        XCTAssertTrue(changes.deletedIdentifiers.isEmpty)
    }

    func testSnapshotStoreDeletesItemsMissingFromAuthoritativeParents() throws {
        let (defaults, suiteName) = makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = HomeOSFileProviderSnapshotStore(defaults: defaults)
        let anchor = store.recordFullEnumeration(
            containerIdentifier: "working-set",
            snapshots: [snapshot("/Downloads/old", parent: "/Downloads", fingerprint: "v1")]
        )

        let changes = try store.changes(
            containerIdentifier: "working-set",
            from: anchor,
            current: [],
            authoritativeParentIdentifiers: ["/Downloads"]
        )

        XCTAssertEqual(changes.deletedIdentifiers, ["/Downloads/old"])
    }

    private func snapshot(
        _ identifier: String,
        parent: String = NSFileProviderItemIdentifier.rootContainer.rawValue,
        fingerprint: String
    ) -> HomeOSFileProviderSnapshot {
        HomeOSFileProviderSnapshot(
            itemIdentifier: identifier,
            parentIdentifier: parent,
            fingerprint: fingerprint
        )
    }

    private func makeDefaults() -> (UserDefaults, String) {
        let suiteName = "HomeOSFileProviderStateStoreTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
        return (defaults, suiteName)
    }
}
