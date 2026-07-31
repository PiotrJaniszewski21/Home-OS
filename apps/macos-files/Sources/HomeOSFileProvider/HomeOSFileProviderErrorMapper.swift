import FileProvider
import Foundation

enum HomeOSFileProviderErrorMapper {
    static func map(_ error: Error) -> Error {
        let nsError = error as NSError
        if nsError.domain == NSFileProviderErrorDomain || nsError.domain == NSCocoaErrorDomain {
            return error
        }

        if error is CancellationError {
            return NSFileProviderError(.serverUnreachable)
        }

        guard let apiError = error as? APIError else {
            return NSFileProviderError(.serverUnreachable)
        }

        switch apiError {
        case .invalidURL:
            return NSFileProviderError(.serverUnreachable)
        case .requestFailed(let message), .decodingFailed(let message):
            if looksUnauthenticated(message) {
                return NSFileProviderError(.notAuthenticated)
            }
            if looksLikeCollision(message) {
                return NSFileProviderError(.filenameCollision)
            }
            return NSFileProviderError(.serverUnreachable)
        }
    }

    private static func looksUnauthenticated(_ message: String) -> Bool {
        let lowercased = message.lowercased()
        return lowercased.contains("unauthorized")
            || lowercased.contains("forbidden")
            || lowercased.contains("not authenticated")
            || lowercased.contains("sign in")
            || lowercased.contains("login")
            || lowercased.contains("html instead of json")
    }

    private static func looksLikeCollision(_ message: String) -> Bool {
        let lowercased = message.lowercased()
        return lowercased.contains("already exists")
            || lowercased.contains("name already taken")
    }
}
