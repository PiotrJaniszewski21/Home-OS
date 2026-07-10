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

        let actions = try XCTUnwrap(extensionDictionary["NSExtensionFileProviderActions"] as? [[String: Any]])
        XCTAssertEqual(actions.map { $0["NSExtensionFileProviderActionIdentifier"] as? String }, [
            "uk.co.petershomenet.homeos.keep-on-disk",
            "uk.co.petershomenet.homeos.stop-keeping-on-disk",
        ])
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
