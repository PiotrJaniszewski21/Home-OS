import Foundation

struct ConnectionEndpoint: Equatable, Sendable {
    enum Kind: String, Sendable {
        case local = "Local"
        case remote = "Domain"
    }

    let kind: Kind
    let url: String

    var displayName: String {
        "\(kind.rawValue) · \(URL(string: url)?.host ?? url)"
    }
}

struct EndpointResolutionFailure: Equatable, Sendable {
    let endpoint: ConnectionEndpoint
    let message: String
}

struct EndpointResolutionResult: Sendable {
    let endpoint: ConnectionEndpoint?
    let failures: [EndpointResolutionFailure]
}

struct ConnectionEndpointResolver: Sendable {
    typealias Probe = @Sendable (ConnectionEndpoint) async throws -> Bool

    static func candidates(domainURL: String, localURL: String, preferLocal: Bool) -> [ConnectionEndpoint] {
        candidates(domainURL: domainURL, localURL: localURL, discoveredLocalURLs: [], preferLocal: preferLocal)
    }

    static func candidates(
        domainURL: String,
        localURL: String,
        discoveredLocalURLs: [String],
        preferLocal: Bool
    ) -> [ConnectionEndpoint] {
        let domain = normalizedURL(domainURL)
        let local = normalizedURL(localURL)
        let discovered = discoveredLocalURLs.compactMap(normalizedURL)
        var candidates: [ConnectionEndpoint] = []

        if preferLocal {
            candidates.append(contentsOf: localEndpoints(manualURL: local, discoveredURLs: discovered))
        }
        if let domain {
            candidates.append(ConnectionEndpoint(kind: .remote, url: domain))
        }
        if !preferLocal {
            candidates.append(contentsOf: localEndpoints(manualURL: local, discoveredURLs: discovered))
        }

        return deduplicated(candidates)
    }

    static func localCandidates(_ urls: [String]) -> [ConnectionEndpoint] {
        deduplicated(urls.compactMap(normalizedURL).map { ConnectionEndpoint(kind: .local, url: $0) })
    }

    func resolve(candidates: [ConnectionEndpoint], probe: Probe) async -> EndpointResolutionResult {
        var failures: [EndpointResolutionFailure] = []
        for endpoint in candidates {
            do {
                if try await probe(endpoint) {
                    return EndpointResolutionResult(endpoint: endpoint, failures: failures)
                }
                failures.append(EndpointResolutionFailure(endpoint: endpoint, message: "Server did not report healthy."))
            } catch {
                failures.append(EndpointResolutionFailure(endpoint: endpoint, message: error.localizedDescription))
            }
        }
        return EndpointResolutionResult(endpoint: nil, failures: failures)
    }

    static func normalizedURL(_ value: String) -> String? {
        let trimmed = value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty,
              var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              scheme == "https",
              components.host != nil
        else {
            return nil
        }
        if components.port == 4443 {
            components.port = nil
        }
        return components.string
    }

    private static func deduplicated(_ candidates: [ConnectionEndpoint]) -> [ConnectionEndpoint] {
        var seen = Set<String>()
        return candidates.filter { candidate in
            seen.insert(candidate.url).inserted
        }
    }

    private static func localEndpoints(manualURL: String?, discoveredURLs: [String]) -> [ConnectionEndpoint] {
        var urls = [String]()
        if let manualURL {
            urls.append(manualURL)
        }
        urls.append(contentsOf: discoveredURLs)
        return urls.map { ConnectionEndpoint(kind: .local, url: $0) }
    }
}
