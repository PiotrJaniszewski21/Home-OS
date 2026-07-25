import Foundation

@MainActor
final class MusicRadioStore: ObservableObject {
    @Published private(set) var featured: [MusicRadioStation] = []
    @Published var results: [MusicRadioStation] = []
    @Published private(set) var favouriteIDs: Set<String> = []
    @Published var isLoading = false
    @Published var error: String?

    private let defaultsKey = "HomeOSMusicRadioFavourites"
    private var savedFavourites: [String: MusicRadioStation] = [:]

    init() {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let stations = try? JSONDecoder().decode([MusicRadioStation].self, from: data) else {
            return
        }
        savedFavourites = Dictionary(uniqueKeysWithValues: stations.map { ($0.id, $0) })
        favouriteIDs = Set(savedFavourites.keys)
    }

    var favourites: [MusicRadioStation] {
        savedFavourites.values.sorted {
            $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    func load(using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            featured = try await client.musicRadioStations()
            refreshSavedStations(from: featured)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func search(_ query: String, using client: APIClient?) async {
        guard let client else { return }
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else {
            results = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            results = try await client.musicRadioStations(query: term)
            refreshSavedStations(from: results)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func toggleFavourite(_ station: MusicRadioStation) {
        if favouriteIDs.contains(station.id) {
            favouriteIDs.remove(station.id)
            savedFavourites.removeValue(forKey: station.id)
        } else {
            favouriteIDs.insert(station.id)
            savedFavourites[station.id] = station
        }
        persistFavourites()
    }

    private func refreshSavedStations(from stations: [MusicRadioStation]) {
        for station in stations where favouriteIDs.contains(station.id) {
            savedFavourites[station.id] = station
        }
        persistFavourites()
    }

    private func persistFavourites() {
        let stations = savedFavourites.values.sorted { $0.name < $1.name }
        if let data = try? JSONEncoder().encode(stations) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
    }
}
