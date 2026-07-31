import Foundation
import XCTest

final class FileProviderConfigurationTests: XCTestCase {
    func testFileProviderItemPublishesUploadStateForFinderBadges() throws {
        let source = try String(
            contentsOf: packageRoot()
                .appendingPathComponent("Sources/HomeOSFileProvider/HomeOSFileProviderItem.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("let isUploaded: Bool"))
        XCTAssertTrue(source.contains("let isUploading: Bool"))
        XCTAssertTrue(source.contains("let uploadingError: Error?"))
        XCTAssertTrue(source.contains("self.isUploaded = isUploaded"))
        XCTAssertTrue(source.contains("self.isUploading = isUploading"))
    }

    func testFileProviderDoesNotAdvertiseUnsupportedTrashCapability() throws {
        let source = try String(
            contentsOf: packageRoot()
                .appendingPathComponent("Sources/HomeOSFileProvider/HomeOSFileProviderItem.swift"),
            encoding: .utf8
        )

        XCTAssertFalse(source.contains(".allowsTrashing"))
        XCTAssertTrue(source.contains(".allowsDeleting"))
    }

    func testFileProviderAllowsLongRunningLargeFileTransfers() throws {
        let source = try String(
            contentsOf: packageRoot()
                .appendingPathComponent("Sources/HomeOSFileProvider/HomeOSFileProviderBackend.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("transferRequestTimeout: TimeInterval = 2 * 60"))
        XCTAssertTrue(source.contains("transferResourceTimeout: TimeInterval = 7 * 24 * 60 * 60"))
        XCTAssertEqual(
            source.components(separatedBy: "timeoutIntervalForRequest = Self.transferRequestTimeout").count - 1,
            2
        )
        XCTAssertEqual(
            source.components(separatedBy: "timeoutIntervalForResource = Self.transferResourceTimeout").count - 1,
            2
        )
        XCTAssertFalse(source.contains("timeoutIntervalForRequest = 3"))
        XCTAssertFalse(source.contains("timeoutIntervalForRequest = 12"))
        XCTAssertFalse(source.contains("timeoutIntervalForResource = 120"))
        XCTAssertFalse(source.contains("timeoutIntervalForResource = 300"))
    }

    func testFileProviderReconcilesCommittedCreateRetries() throws {
        let backend = try String(
            contentsOf: packageRoot()
                .appendingPathComponent("Sources/HomeOSFileProvider/HomeOSFileProviderBackend.swift"),
            encoding: .utf8
        )
        let fileProviderExtension = try String(
            contentsOf: packageRoot()
                .appendingPathComponent("Sources/HomeOSFileProvider/HomeOSFileProviderExtension.swift"),
            encoding: .utf8
        )
        let errorMapper = try String(
            contentsOf: packageRoot()
                .appendingPathComponent("Sources/HomeOSFileProvider/HomeOSFileProviderErrorMapper.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(fileProviderExtension.contains("options.contains(.mayAlreadyExist)"))
        XCTAssertTrue(fileProviderExtension.contains("mayAlreadyExist: mayAlreadyExist"))
        XCTAssertTrue(backend.contains("Reconciled committed File Provider directory retry"))
        XCTAssertTrue(backend.contains("NSError.fileProviderErrorForCollision(with: existing)"))
        XCTAssertTrue(errorMapper.contains("NSFileProviderError(.filenameCollision)"))
    }

    func testFileProviderInfoPlistKeepsFinderMenuActionsEnabled() throws {
        let plistURL = packageRoot()
            .appendingPathComponent("Resources/HomeOSFileProvider-Info.plist")
        let data = try Data(contentsOf: plistURL)
        let plist = try XCTUnwrap(
            PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any]
        )
        let extensionDictionary = try XCTUnwrap(plist["NSExtension"] as? [String: Any])

        XCTAssertEqual(extensionDictionary["NSExtensionFileProviderAllowsContextualMenuDownloadEntry"] as? Bool, true)
        XCTAssertEqual(extensionDictionary["NSExtensionFileProviderAllowsUserControlledEviction"] as? Bool, true)
        XCTAssertNil(extensionDictionary["NSExtensionFileProviderDocumentGroup"])

        let actions = try XCTUnwrap(extensionDictionary["NSExtensionFileProviderActions"] as? [[String: Any]])
        XCTAssertEqual(actions.map { $0["NSExtensionFileProviderActionIdentifier"] as? String }, [
            "uk.co.petershomenet.homeos.keep-on-disk",
            "uk.co.petershomenet.homeos.stop-keeping-on-disk",
        ])
        let activationRules = actions.compactMap { $0["NSExtensionFileProviderActionActivationRule"] as? String }
        XCTAssertTrue(activationRules[0].contains("$item.userInfo.homeosKeptOnDisk != YES"))
        XCTAssertTrue(activationRules[1].contains("$item.userInfo.homeosKeptOnDisk == YES"))
    }

    func testLocalNetworkPermissionIsDeclaredForAppAndProvider() throws {
        let appPlist = try plist(named: "HomeOS-Info.plist")
        let providerPlist = try plist(named: "HomeOSFileProvider-Info.plist")

        XCTAssertEqual(
            appPlist["NSLocalNetworkUsageDescription"] as? String,
            "Home OS connects directly to your home server on your local network for faster file transfers."
        )
        XCTAssertEqual(
            providerPlist["NSLocalNetworkUsageDescription"] as? String,
            "Home OS connects directly to your home server on your local network for faster file transfers."
        )
    }

    func testPinnedLocalCertificateIsDeclaredForAppAndProvider() throws {
        let expected = "2D9AB481E4FB226594AE44219B6B94408CA0C764ECA8886751D49830A88613C0"
        XCTAssertEqual(try plist(named: "HomeOS-Info.plist")["HomeOSLocalCertificateSHA256"] as? String, expected)
        XCTAssertEqual(try plist(named: "HomeOSFileProvider-Info.plist")["HomeOSLocalCertificateSHA256"] as? String, expected)
    }

    func testBothAppsAndFileProviderShareHomeOSIconCatalogue() throws {
        let project = try String(
            contentsOf: packageRoot().appendingPathComponent("project.yml"),
            encoding: .utf8
        )

        XCTAssertEqual(project.components(separatedBy: "ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon").count - 1, 3)
        XCTAssertEqual(project.components(separatedBy: "path: Resources/Assets.xcassets").count - 1, 3)
    }

    private func packageRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func plist(named name: String) throws -> [String: Any] {
        let plistURL = packageRoot()
            .appendingPathComponent("Resources")
            .appendingPathComponent(name)
        let data = try Data(contentsOf: plistURL)
        return try XCTUnwrap(
            PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any]
        )
    }
}
