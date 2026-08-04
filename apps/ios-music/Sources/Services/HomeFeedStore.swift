import CryptoKit
import Foundation

enum HomeFeedStore {
    private static let fileManager = FileManager.default

    static func load(for client: APIClient) -> HomeMusicFeed? {
        guard let data = try? Data(contentsOf: fileURL(for: client)) else {
            return nil
        }
        return try? JSONDecoder().decode(HomeMusicFeed.self, from: data)
    }

    static func save(_ feed: HomeMusicFeed, for client: APIClient) throws {
        let directory = try cacheDirectory()
        let url = directory.appendingPathComponent(filename(for: client))
        let data = try JSONEncoder().encode(feed)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    static func namespace(for client: APIClient) -> String {
        let identity = "\(client.baseURL.absoluteString)|\(client.token)"
        return SHA256.hash(data: Data(identity.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private static func fileURL(for client: APIClient) -> URL {
        let directory = (
            try? cacheDirectory()
        ) ?? fileManager.temporaryDirectory
        return directory.appendingPathComponent(filename(for: client))
    }

    private static func cacheDirectory() throws -> URL {
        let base = try fileManager.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let directory = base.appendingPathComponent(
            "HomeMusic/ListenNow",
            isDirectory: true
        )
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        return directory
    }

    private static func filename(for client: APIClient) -> String {
        "\(namespace(for: client)).json"
    }
}
