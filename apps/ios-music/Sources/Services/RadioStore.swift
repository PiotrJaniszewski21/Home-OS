import Foundation

@MainActor
final class RadioStore: ObservableObject {
    @Published private(set) var featured: [RadioStation] = []
    @Published var results: [RadioStation] = []
    @Published private(set) var favouriteIDs: Set<String> = []
    @Published var isLoading = false
    @Published var error: String?

    private let defaultsKey = "HomeMusicRadioFavourites"
    private var savedFavourites: [String: RadioStation] = [:]

    init() {
        if let data = UserDefaults.standard.data(forKey: defaultsKey),
           let stations = try? JSONDecoder().decode([RadioStation].self, from: data) {
            savedFavourites = Dictionary(uniqueKeysWithValues: stations.map { ($0.id, $0) })
            favouriteIDs = Set(savedFavourites.keys)
        }
    }

    var favourites: [RadioStation] {
        savedFavourites.values.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    func load(using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            featured = try await client.radioStations()
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
            results = try await client.radioStations(query: term)
            refreshSavedStations(from: results)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func toggleFavourite(_ station: RadioStation) {
        if favouriteIDs.contains(station.id) {
            favouriteIDs.remove(station.id)
            savedFavourites.removeValue(forKey: station.id)
        } else {
            favouriteIDs.insert(station.id)
            savedFavourites[station.id] = station
        }
        persistFavourites()
    }

    private func refreshSavedStations(from stations: [RadioStation]) {
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
