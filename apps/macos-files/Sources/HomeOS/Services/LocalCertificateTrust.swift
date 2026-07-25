import CryptoKit
import Foundation
import Security

enum LocalCertificateTrustPolicy {
    static func allowsSelfSignedCertificate(forHost host: String) -> Bool {
        let normalizedHost = host
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
            .lowercased()

        guard !normalizedHost.isEmpty else { return false }

        if normalizedHost == "localhost" || normalizedHost.hasSuffix(".local") {
            return true
        }

        guard let octets = ipv4Octets(from: normalizedHost) else { return false }
        return isPrivateOrLocalIPv4(octets)
    }

    static func shouldTrust(challenge: URLAuthenticationChallenge) -> Bool {
        guard
            challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            allowsSelfSignedCertificate(forHost: challenge.protectionSpace.host),
            let trust = challenge.protectionSpace.serverTrust,
            let certificateChain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
            let certificate = certificateChain.first,
            let configuredFingerprint = configuredFingerprint
        else {
            return false
        }
        let certificateData = SecCertificateCopyData(certificate) as Data
        let fingerprint = SHA256.hash(data: certificateData)
            .map { String(format: "%02X", $0) }
            .joined()
        return fingerprint == configuredFingerprint
    }

    private static var configuredFingerprint: String? {
        guard let rawValue = Bundle.main.object(forInfoDictionaryKey: "HomeOSLocalCertificateSHA256") as? String else {
            return nil
        }
        let normalized = rawValue
            .filter(\.isHexDigit)
            .uppercased()
        return normalized.count == 64 ? normalized : nil
    }

    private static func ipv4Octets(from host: String) -> [Int]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }

        var octets: [Int] = []
        for part in parts {
            guard
                !part.isEmpty,
                part.allSatisfy(\.isNumber),
                let value = Int(part),
                (0...255).contains(value)
            else {
                return nil
            }
            octets.append(value)
        }
        return octets
    }

    private static func isPrivateOrLocalIPv4(_ octets: [Int]) -> Bool {
        guard octets.count == 4 else { return false }
        let first = octets[0]
        let second = octets[1]

        return first == 10
            || first == 127
            || (first == 169 && second == 254)
            || (first == 172 && (16...31).contains(second))
            || (first == 192 && second == 168)
            || (first == 100 && (64...127).contains(second))
    }
}

final class LocalCertificateTrustDelegate: NSObject, URLSessionDelegate, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard
            LocalCertificateTrustPolicy.shouldTrust(challenge: challenge),
            let trust = challenge.protectionSpace.serverTrust
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard let source = task.originalRequest?.url, let destination = request.url else {
            completionHandler(nil)
            return
        }
        let sameOrigin = source.scheme?.lowercased() == "https"
            && destination.scheme?.lowercased() == "https"
            && source.host?.lowercased() == destination.host?.lowercased()
            && source.port == destination.port
        completionHandler(sameOrigin ? request : nil)
    }
}
