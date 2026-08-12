import CryptoKit
import Foundation
import UIKit

actor ArtworkCacheStore {
    static let shared = ArtworkCacheStore()

    private static let maximumBytes: Int64 = 400 * 1024 * 1024
    private static let targetBytes: Int64 = 320 * 1024 * 1024
    private static let maximumAge: TimeInterval = 30 * 24 * 60 * 60
    private static let maximumImageBytes = 12 * 1024 * 1024
    private let memoryCache: NSCache<NSString, UIImage>

    init() {
        memoryCache = NSCache<NSString, UIImage>()
        memoryCache.countLimit = 300
        memoryCache.totalCostLimit = 96 * 1024 * 1024
    }

    func cachedImage(for url: URL) -> UIImage? {
        let key = url.absoluteString as NSString
        if let image = memoryCache.object(forKey: key) {
            return image
        }
        let path = fileURL(for: url)
        if let data = try? Data(contentsOf: path),
           let image = UIImage(data: data) {
            memoryCache.setObject(image, forKey: key, cost: image.memoryCost)
            return image
        }
        return nil
    }

    func image(for url: URL) async -> UIImage? {
        let key = url.absoluteString as NSString
        if let image = memoryCache.object(forKey: key) {
            return image
        }
        let path = fileURL(for: url)
        if let data = try? Data(contentsOf: path),
           let image = UIImage(data: data) {
            try? FileManager.default.setAttributes(
                [.modificationDate: Date()],
                ofItemAtPath: path.path
            )
            memoryCache.setObject(image, forKey: key, cost: image.memoryCost)
            return image
        }

        guard let (data, response) = try? await URLSession.shared.data(from: url),
              data.count >= 512,
              data.count <= Self.maximumImageBytes,
              let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode),
              let image = UIImage(data: data) else {
            return nil
        }
        let contentType = http.value(forHTTPHeaderField: "Content-Type")?
            .lowercased() ?? ""
        guard !contentType.contains("html"), !contentType.contains("json") else {
            return nil
        }
        do {
            try ensureDirectory()
            try data.write(
                to: path,
                options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
            )
            prune()
        } catch {}
        memoryCache.setObject(image, forKey: key, cost: image.memoryCost)
        return image
    }

    private func ensureDirectory() throws {
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
    }

    private var directory: URL {
        let base = FileManager.default.urls(
            for: .cachesDirectory,
            in: .userDomainMask
        )[0]
        return base.appending(
            path: "HomeMusic/Artwork",
            directoryHint: .isDirectory
        )
    }

    private func fileURL(for url: URL) -> URL {
        let digest = SHA256.hash(data: Data(url.absoluteString.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return directory.appending(path: "\(digest).image")
    }

    private func prune() {
        guard let paths = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [
                .contentModificationDateKey,
                .fileSizeKey,
            ],
            options: [.skipsHiddenFiles]
        ) else { return }
        let cutoff = Date().addingTimeInterval(-Self.maximumAge)
        var files: [(URL, Int64, Date)] = []
        for path in paths {
            guard let values = try? path.resourceValues(
                forKeys: [.contentModificationDateKey, .fileSizeKey]
            ) else { continue }
            let modified = values.contentModificationDate ?? .distantPast
            if modified < cutoff {
                try? FileManager.default.removeItem(at: path)
                continue
            }
            files.append((path, Int64(values.fileSize ?? 0), modified))
        }
        var totalBytes = files.reduce(Int64(0)) { $0 + $1.1 }
        guard totalBytes > Self.maximumBytes else { return }
        for (path, size, _) in files.sorted(by: { $0.2 < $1.2 })
        where totalBytes > Self.targetBytes {
            try? FileManager.default.removeItem(at: path)
            totalBytes -= size
        }
    }
}

private extension UIImage {
    var memoryCost: Int {
        guard let cgImage else { return 0 }
        return cgImage.bytesPerRow * cgImage.height
    }
}
