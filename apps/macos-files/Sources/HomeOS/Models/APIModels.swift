import Foundation

struct LoginResponse: Decodable {
    let ok: Bool
    let data: LoginData?
    let error: String?
}

struct LoginData: Decodable {
    let token: String
    let user: UserInfo
}

struct UserInfo: Decodable {
    let username: String
    let role: String
}

struct BasicResponse: Decodable {
    let ok: Bool
    let error: String?
}

struct UploadResponse: Decodable {
    struct Payload: Decodable {
        let uploaded: [FileEntry]

        private enum CodingKeys: String, CodingKey {
            case uploaded
            case entries
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            uploaded = try container.decodeIfPresent([FileEntry].self, forKey: .uploaded)
                ?? container.decodeIfPresent([FileEntry].self, forKey: .entries)
                ?? []
        }
    }

    let ok: Bool
    let data: Payload?
    let error: String?
}

struct HealthResponse: Decodable {
    let status: String
    let version: String?
}

struct FileListResponse: Decodable {
    let ok: Bool
    let data: FileListData?
    let error: String?
}

struct FileListData: Decodable, Sendable {
    let path: String
    let entries: [FileEntry]
}

struct FileEntry: Decodable, Identifiable, Hashable, Sendable {
    let name: String
    let path: String
    let isDirectory: Bool
    let size: Int64?
    let modified: String?
    let extensionType: String?

    var id: String { path }

    enum CodingKeys: String, CodingKey {
        case name
        case path
        case isDirectory = "is_dir"
        case size
        case modified
        case extensionType = "extension"
    }
}

struct SearchResponse: Decodable {
    let ok: Bool
    let data: [FileEntry]?
    let error: String?
}

struct MetricsResponse: Decodable {
    let ok: Bool
    let data: MetricsData?
    let error: String?
}

struct MetricsData: Decodable {
    let cpuPercent: Double
    let cpuCount: Int
    let memory: MemoryData
    let disk: DiskData
    let network: NetworkData
    let uptime: String
    let uptimeSeconds: Int?
    let platform: String?
    let hostname: String
    let pythonVersion: String?

    enum CodingKeys: String, CodingKey {
        case cpuPercent = "cpu_percent"
        case cpuCount = "cpu_count"
        case memory
        case disk
        case network
        case uptime
        case uptimeSeconds = "uptime_seconds"
        case platform
        case hostname
        case pythonVersion = "python_version"
    }
}

struct NetworkSpeedResponse: Decodable {
    let ok: Bool
    let data: NetworkSpeedSample?
    let error: String?
}

struct NetworkSpeedSample: Decodable {
    let bytesSent: Int64
    let bytesReceived: Int64
    let timestamp: TimeInterval

    enum CodingKeys: String, CodingKey {
        case bytesSent = "bytes_sent"
        case bytesReceived = "bytes_recv"
        case timestamp
    }
}

struct MemoryData: Decodable {
    let totalGB: Double
    let usedGB: Double
    let percent: Double

    enum CodingKeys: String, CodingKey {
        case totalGB = "total_gb"
        case usedGB = "used_gb"
        case percent
    }
}

struct DiskData: Decodable {
    let totalGB: Double
    let usedGB: Double
    let percent: Double

    enum CodingKeys: String, CodingKey {
        case totalGB = "total_gb"
        case usedGB = "used_gb"
        case percent
    }
}

struct NetworkData: Decodable {
    let sentGB: Double
    let recvGB: Double

    enum CodingKeys: String, CodingKey {
        case sentGB = "sent_gb"
        case recvGB = "recv_gb"
    }
}

struct StorageResponse: Decodable {
    let ok: Bool
    let data: StorageData?
    let error: String?
}

struct StorageData: Decodable {
    let main: MainStorageData
    let drives: [DriveData]
}

struct MainStorageData: Decodable {
    let totalBytes: Int64
    let usedBytes: Int64
    let freeBytes: Int64
    let percentUsed: Double

    enum CodingKeys: String, CodingKey {
        case totalBytes = "total_bytes"
        case usedBytes = "used_bytes"
        case freeBytes = "free_bytes"
        case percentUsed = "percent_used"
    }
}

struct DriveData: Decodable, Identifiable, Hashable {
    let name: String
    let device: String?
    let mountPoint: String
    let filesystem: String?
    let totalBytes: Int64
    let usedBytes: Int64
    let freeBytes: Int64
    let percentUsed: Double
    let removable: Bool?

    var id: String { mountPoint }

    enum CodingKeys: String, CodingKey {
        case name
        case device
        case mountPoint = "mount_point"
        case filesystem
        case totalBytes = "total_bytes"
        case usedBytes = "used_bytes"
        case freeBytes = "free_bytes"
        case percentUsed = "percent_used"
        case removable
    }
}

struct ConnectionInfoResponse: Decodable {
    let ok: Bool
    let data: ConnectionInfoData?
    let error: String?
}

struct ConnectionInfoData: Decodable {
    let hostname: String?
    let port: Int?
    let localURLs: [String]

    enum CodingKeys: String, CodingKey {
        case hostname
        case port
        case localURLs = "local_urls"
    }
}
