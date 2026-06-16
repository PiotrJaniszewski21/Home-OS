import Foundation

// MARK: - Auth

struct LoginResponse: Decodable {
    let ok: Bool
    let data: LoginData?
    let error: ErrorData?
}

struct LoginData: Decodable {
    let token: String
    let user: UserInfo
}

struct UserInfo: Decodable {
    let username: String
    let role: String
}

struct ErrorData: Decodable {
    let message: String?
}

// MARK: - Files

struct FileListResponse: Decodable {
    let ok: Bool
    let data: FileListData?
}

struct FileListData: Decodable {
    let path: String
    let entries: [FileEntry]
}

struct FileEntry: Decodable, Identifiable {
    let name: String
    let path: String
    let is_dir: Bool
    let size: Int?
    let modified: String?
    let extension_type: String?

    var id: String { path }

    enum CodingKeys: String, CodingKey {
        case name, path, is_dir, size, modified
        case extension_type = "extension"
    }
}

struct SearchResponse: Decodable {
    let ok: Bool
    let data: [FileEntry]?
}

// MARK: - Monitor

struct MetricsResponse: Decodable {
    let ok: Bool
    let data: MetricsData?
}

struct MetricsData: Decodable {
    let cpu_percent: Double
    let cpu_count: Int
    let memory: MemoryData
    let disk: DiskData
    let network: NetworkData
    let uptime: String
    let hostname: String
}

struct MemoryData: Decodable {
    let total_gb: Double
    let used_gb: Double
    let percent: Double
}

struct DiskData: Decodable {
    let total_gb: Double
    let used_gb: Double
    let percent: Double
}

struct NetworkData: Decodable {
    let sent_gb: Double
    let recv_gb: Double
}

// MARK: - Storage

struct StorageResponse: Decodable {
    let ok: Bool
    let data: StorageData?
}

struct StorageData: Decodable {
    let main: MainStorageData
    let drives: [DriveData]
}

struct MainStorageData: Decodable {
    let total_bytes: Int
    let used_bytes: Int
    let free_bytes: Int
    let percent_used: Double
}

struct DriveData: Decodable {
    let name: String
    let device: String
    let mount_point: String
    let filesystem: String
    let total_bytes: Int
    let used_bytes: Int
    let free_bytes: Int
    let percent_used: Double
}

// MARK: - AI

struct AIResponse: Decodable {
    let ok: Bool
    let data: AIResponseData?
    let error: String?
}

struct AIResponseData: Decodable {
    let response: String
}
