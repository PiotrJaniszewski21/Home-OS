import Foundation

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
        challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust
            && challenge.protectionSpace.serverTrust != nil
            && allowsSelfSignedCertificate(forHost: challenge.protectionSpace.host)
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

final class LocalCertificateTrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
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
}
