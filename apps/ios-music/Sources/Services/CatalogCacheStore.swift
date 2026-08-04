import CryptoKit
import Foundation

private struct CatalogCacheEntry<Value: Codable>: Codable {
    let savedAt: Date
    let value: Value
}

actor CatalogCacheStore {
    static let shared = CatalogCacheStore()

    func load<Value: Codable>(
        _ type: Value.Type,
        key: String,
        client: APIClient,
        maximumAge: TimeInterval
    ) -> Value? {
        let url = fileURL(key: key, client: client)
        guard let data = try? Data(contentsOf: url),
              let entry = try? JSONDecoder().decode(
                CatalogCacheEntry<Value>.self,
                from: data
              ),
              Date().timeIntervalSince(entry.savedAt) <= maximumAge else {
            return nil
        }
        return entry.value
    }

    func save<Value: Codable>(
        _ value: Value,
        key: String,
        client: APIClient
    ) {
        do {
            let directory = cacheDirectory(client: client)
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
            let data = try JSONEncoder().encode(
                CatalogCacheEntry(savedAt: Date(), value: value)
            )
            try data.write(
                to: fileURL(key: key, client: client),
                options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
            )
        } catch {}
    }

    private func cacheDirectory(client: APIClient) -> URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appending(path: "HomeMusic/Catalog", directoryHint: .isDirectory)
            .appending(
                path: HomeFeedStore.namespace(for: client),
                directoryHint: .isDirectory
            )
    }

    private func fileURL(key: String, client: APIClient) -> URL {
        let digest = SHA256.hash(data: Data(key.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return cacheDirectory(client: client).appending(path: "\(digest).json")
    }
}
