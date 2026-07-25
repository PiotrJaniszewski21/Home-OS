import Foundation

struct MultipartFormFile {
    let url: URL
    let contentLength: Int64

    static func create(
        destinationPath: String,
        fileURL: URL,
        requestedFilename: String?,
        boundary: String,
        fileManager: FileManager = .default
    ) throws -> MultipartFormFile {
        let resourceValues = try fileURL.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey])
        guard resourceValues.isSymbolicLink != true, resourceValues.isRegularFile == true else {
            throw APIError.requestFailed("Symbolic links and non-regular files cannot be uploaded.")
        }
        let filename = sanitizedFilename(requestedFilename, fallback: fileURL.lastPathComponent)
        let bodyURL = fileManager.temporaryDirectory
            .appendingPathComponent("homeos-multipart-\(UUID().uuidString).body")

        guard fileManager.createFile(atPath: bodyURL.path, contents: nil) else {
            throw APIError.requestFailed("Could not prepare upload data.")
        }

        let startedSecurityScope = fileURL.startAccessingSecurityScopedResource()
        defer {
            if startedSecurityScope {
                fileURL.stopAccessingSecurityScopedResource()
            }
        }

        do {
            let output = try FileHandle(forWritingTo: bodyURL)
            defer { try? output.close() }

            try output.write(contentsOf: Data("--\(boundary)\r\n".utf8))
            try output.write(contentsOf: Data("Content-Disposition: form-data; name=\"path\"\r\n\r\n".utf8))
            try output.write(contentsOf: Data("\(destinationPath)\r\n".utf8))
            try output.write(contentsOf: Data("--\(boundary)\r\n".utf8))
            try output.write(contentsOf: Data("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8))
            try output.write(contentsOf: Data("Content-Type: application/octet-stream\r\n\r\n".utf8))

            let input = try FileHandle(forReadingFrom: fileURL)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 1_048_576), !chunk.isEmpty {
                try output.write(contentsOf: chunk)
            }

            try output.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
            try output.synchronize()

            let attributes = try fileManager.attributesOfItem(atPath: bodyURL.path)
            let size = (attributes[.size] as? NSNumber)?.int64Value ?? 0
            return MultipartFormFile(url: bodyURL, contentLength: size)
        } catch {
            try? fileManager.removeItem(at: bodyURL)
            throw error
        }
    }

    private static func sanitizedFilename(_ requested: String?, fallback: String) -> String {
        let value = requested?
            .split(separator: "/")
            .last
            .map(String.init)
            .flatMap { $0.isEmpty ? nil : $0 }
            ?? fallback
        return value
            .replacingOccurrences(of: "\r", with: "_")
            .replacingOccurrences(of: "\n", with: "_")
            .replacingOccurrences(of: "\"", with: "_")
    }
}
